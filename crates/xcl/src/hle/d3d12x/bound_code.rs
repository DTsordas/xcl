//! `XCL_BOUND_CODE=1`: how much shader code the title's lists have bound so far, for the device
//! removals at world load (#505). A pipeline counts once, at the first `SetPipelineState` that
//! binds it, with the bytes of DXIL it was built from and the size of its driver blob
//! (`GetCachedBlob`; on AMD a small stub, on NVIDIA the driver's code). Never subtracted.
//! Logged every 10 MB of DXIL and at a device removal.

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use windows::core::Interface;
use windows::Win32::Graphics::Direct3D12::ID3D12PipelineState;

pub(super) fn on() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var("XCL_BOUND_CODE").is_ok_and(|v| v.trim() == "1"))
}

thread_local! {
    /// bytes of DXIL of the pipeline this thread is building (`driver_compile_identified`)
    static BUILDING: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// The pipeline the driver builds next on this thread has `bytes` of DXIL (0: not known).
pub(super) fn building(bytes: usize) {
    if on() {
        BUILDING.with(|b| b.set(bytes));
    }
}

#[derive(Default)]
struct Totals {
    /// pipeline → bytes of DXIL it was built from
    dxil_of: HashMap<usize, usize>,
    counted: HashSet<usize>,
    dxil: u64,
    blob: u64,
    pipelines: u64,
    big: u64,
    next_log: u64,
}

impl Totals {
    /// Counts `pso` (with `blob` bytes of driver blob) unless it was counted already; Some(line)
    /// when the DXIL total passed the next 10 MB step.
    fn count(&mut self, pso: usize, blob: u64) -> Option<String> {
        if !self.counted.insert(pso) {
            return None;
        }
        let dxil = self.dxil_of.get(&pso).copied().unwrap_or(0) as u64;
        self.dxil += dxil;
        self.blob += blob;
        self.pipelines += 1;
        if dxil >= 1 << 20 {
            self.big += 1;
        }
        if self.next_log == 0 {
            self.next_log = STEP;
        }
        (self.dxil >= self.next_log).then(|| {
            self.next_log = (self.dxil / STEP + 1) * STEP;
            line(self)
        })
    }
}

static TOTALS: Mutex<Option<Totals>> = Mutex::new(None);
const STEP: u64 = 10 << 20;

/// A pipeline was made on this thread (its DXIL bytes from `building`).
pub(super) fn created(pso: usize) {
    if !on() || pso == 0 {
        return;
    }
    let bytes = BUILDING.with(|b| b.replace(0));
    let mut g = TOTALS.lock().unwrap();
    let t = g.get_or_insert_with(Default::default);
    t.dxil_of.insert(pso, bytes);
    t.counted.remove(&pso);
}

/// `pso` is bound on a list: counted the first time.
pub(super) fn bound(pso: usize) {
    if !on() || pso == 0 {
        return;
    }
    {
        let g = TOTALS.lock().unwrap();
        if g.as_ref().is_some_and(|t| t.counted.contains(&pso)) {
            return;
        }
    }
    // the blob's size outside the lock (the driver serializes the pipeline)
    let blob = unsafe { ID3D12PipelineState::from_raw_borrowed(&(pso as *mut std::ffi::c_void)) }
        .and_then(|p| unsafe { p.GetCachedBlob() }.ok())
        .map_or(0, |b| unsafe { b.GetBufferSize() } as u64);
    let logged = TOTALS.lock().unwrap().get_or_insert_with(Default::default).count(pso, blob);
    if let Some(line) = logged {
        crate::xlog_info!("D3D12.X: bound shader code: {line}");
    }
}

fn line(t: &Totals) -> String {
    format!(
        "{:.1} MB of DXIL in {} pipelines ({} of 1 MB or more), driver blobs {:.1} MB",
        t.dxil as f64 / (1 << 20) as f64,
        t.pipelines,
        t.big,
        t.blob as f64 / (1 << 20) as f64
    )
}

/// The totals so far (None: `XCL_BOUND_CODE` off or nothing bound).
pub fn report() -> Option<String> {
    if !on() {
        return None;
    }
    TOTALS.lock().unwrap().as_ref().filter(|t| t.pipelines > 0).map(line)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pipeline_counts_once_with_its_dxil() {
        let mut t = Totals::default();
        t.dxil_of.insert(1, 3 << 20);
        t.dxil_of.insert(2, 1000);
        t.dxil_of.insert(3, 8 << 20);
        assert_eq!(t.count(1, 954), None);
        assert_eq!(t.count(1, 954), None, "a second bind of the same pipeline does not count");
        assert_eq!(t.count(2, 954), None);
        assert_eq!((t.pipelines, t.big, t.dxil, t.blob), (2, 1, (3 << 20) + 1000, 2 * 954));
        // passing 10 MB logs once, then not again before 20 MB
        let logged = t.count(3, 0).expect("passed 10 MB");
        assert!(logged.starts_with("11.0 MB of DXIL in 3 pipelines (2 of 1 MB or more)"), "{logged}");
        assert_eq!(t.next_log, 20 << 20);
    }
}
