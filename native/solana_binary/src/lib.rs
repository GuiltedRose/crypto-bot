//! Binary structure only: no execution, address loading, program decoding or trust.
#![deny(unsafe_code)]
#[cfg(feature = "rpc-engine")]
mod rpc;
#[cfg(feature = "rpc-engine")]
mod borsh;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyBytesMethods, PyList, PyListMethods};

type Instruction = (u8, Vec<u8>, Vec<u8>);
type Lookup = (Vec<u8>, Vec<u8>, Vec<u8>);
type Parsed = (i16, Vec<Vec<u8>>, (u8,u8,u8), Vec<Vec<u8>>, Vec<u8>, Vec<Instruction>, Vec<Lookup>);
type ParsedView<'a> = (i16,Vec<&'a[u8]>,(u8,u8,u8),Vec<&'a[u8]>,&'a[u8],Vec<(u8,&'a[u8],&'a[u8])>,Vec<(&'a[u8],&'a[u8],&'a[u8])>);

struct Reader<'a> { raw: &'a [u8], offset: usize }
impl<'a> Reader<'a> {
    fn slice(&mut self, count: usize) -> Result<&'a [u8], &'static str> {
        let end = self.offset.checked_add(count).ok_or("length overflow")?;
        let data = self.raw.get(self.offset..end).ok_or("truncated transaction")?;
        self.offset = end;
        Ok(data)
    }
    fn byte(&mut self) -> Result<u8, &'static str> {
        let value = *self.raw.get(self.offset).ok_or("truncated transaction")?;
        self.offset += 1;
        Ok(value)
    }
    fn short(&mut self, limit: usize) -> Result<usize, &'static str> {
        let mut value = 0usize;
        for index in 0..3 {
            let byte = self.byte()?;
            if index == 2 && byte > 3 { return Err("invalid compact-u16"); }
            value |= ((byte & 127) as usize) << (7 * index);
            if byte & 128 == 0 {
                if index > 0 && byte == 0 { return Err("noncanonical compact-u16"); }
                if value > limit { return Err("length exceeds limit"); }
                return Ok(value);
            }
        }
        Err("invalid compact-u16")
    }
}

fn parse(raw: &[u8]) -> Result<ParsedView<'_>, &'static str> {
    if raw.is_empty() || raw.len() > 1232 { return Err("transaction size outside supported packet limit"); }
    let mut r = Reader { raw, offset: 0 };
    let count = r.short(19)?;
    let mut signatures = Vec::with_capacity(count);
    for _ in 0..count { signatures.push(r.slice(64)?); }
    let first = r.byte()?;
    let (version, required) = if first & 128 != 0 {
        if first != 128 { return Err("unsupported message version"); }
        (0, r.byte()?)
    } else { (-1, first) };
    let header = (required, r.byte()?, r.byte()?);
    let count = r.short(256)?;
    if required == 0 || required as usize != signatures.len() || required as usize > count
        || header.1 >= required || header.2 as usize > count - required as usize {
        return Err("invalid message header");
    }
    let mut keys = Vec::with_capacity(count);
    for _ in 0..count { keys.push(r.slice(32)?); }
    let blockhash = r.slice(32)?;
    let count = r.short(1232)?;
    let mut instructions = Vec::with_capacity(count.min(raw.len()/3));
    for _ in 0..count {
        let program = r.byte()?;
        if program as usize >= keys.len() { return Err("program index outside static keys"); }
        let count = r.short(256)?;
        let accounts = r.slice(count)?;
        let count = r.short(1232)?;
        instructions.push((program, accounts, r.slice(count)?));
    }
    let mut lookups = Vec::new();
    let mut loaded = 0usize;
    if version == 0 {
        let count = r.short(256)?;
        for _ in 0..count {
            let key = r.slice(32)?;
            let count = r.short(256)?;
            let writable = r.slice(count)?;
            let count = r.short(256)?;
            let readonly = r.slice(count)?;
            loaded += writable.len() + readonly.len();
            if loaded + keys.len() > 256 { return Err("too many account indices"); }
            lookups.push((key, writable, readonly));
        }
    }
    if r.offset != raw.len() { return Err("trailing transaction bytes"); }
    for (_, accounts, _) in &instructions {
        if accounts.iter().any(|i| *i as usize >= keys.len()+loaded) { return Err("account index out of bounds"); }
    }
    Ok((version, signatures, header, keys, blockhash, instructions, lookups))
}

#[pyfunction]
fn parse_transaction(raw: &[u8]) -> PyResult<Parsed> {
    let (version,signatures,header,keys,blockhash,instructions,lookups)=parse(raw).map_err(PyValueError::new_err)?;
    Ok((version,signatures.into_iter().map(|v|v.to_vec()).collect(),header,
        keys.into_iter().map(|v|v.to_vec()).collect(),blockhash.to_vec(),
        instructions.into_iter().map(|(p,a,d)|(p,a.to_vec(),d.to_vec())).collect(),
        lookups.into_iter().map(|(k,w,r)|(k.to_vec(),w.to_vec(),r.to_vec())).collect()))
}

/// Offline measurements only. No networking or execution authority.
#[pyfunction]
fn benchmark_parse(packets: &Bound<'_, PyList>, iterations: usize, batch: usize) -> PyResult<(u128,u128,u128)> {
    if packets.is_empty() || packets.len()>64 || !(1..=1000).contains(&iterations) || ![1,64].contains(&batch) {
        return Err(PyValueError::new_err("invalid bounded benchmark"));
    }
    let mut inputs = Vec::with_capacity(packets.len());
    for item in packets.iter() {
        let raw = item.downcast::<PyBytes>()?.as_bytes();
        parse(raw).map_err(PyValueError::new_err)?;
        inputs.push(raw.to_vec());
    }
    let mut samples = Vec::with_capacity(iterations*batch);
    let total = std::time::Instant::now();
    for _ in 0..iterations {
        for index in 0..batch {
            let start = std::time::Instant::now();
            std::hint::black_box(parse(&inputs[index%inputs.len()]).map_err(PyValueError::new_err)?);
            samples.push(start.elapsed().as_nanos());
        }
    }
    let elapsed = total.elapsed().as_nanos();
    samples.sort_unstable();
    Ok((elapsed,samples[samples.len()/2],samples[(samples.len()-1)*95/100]))
}

#[pyfunction]
fn binding_input_len(raw: &[u8]) -> PyResult<usize> {
    if raw.len()>1232 { return Err(PyValueError::new_err("packet too large")); }
    Ok(raw.len())
}

#[pymodule]
fn _solana_binary(module: &Bound<'_, PyModule>) -> PyResult<()> {
    #[cfg(feature = "rpc-engine")]
    rpc::register(module)?;
    module.add_function(wrap_pyfunction!(benchmark_parse, module)?)?;
    module.add_function(wrap_pyfunction!(binding_input_len, module)?)?;
    module.add_function(wrap_pyfunction!(parse_transaction, module)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn malformed_lengths() {
        for raw in [&[0x80,0][..], &[0xff,0xff,4][..], &[0x80][..]] {
            assert!(Reader {raw, offset:0}.short(65535).is_err());
        }
    }
    #[test]
    fn field_views_borrow_packet_memory() {
        let mut raw=vec![1]; raw.extend([0;64]); raw.extend([1,0,0,1]);
        raw.extend([1;32]); raw.extend([2;32]); raw.push(0);
        let parsed=parse(&raw).unwrap();
        assert_eq!(parsed.3[0].as_ptr(),raw[69..].as_ptr());
        assert_eq!(parsed.1[0].as_ptr(),raw[1..].as_ptr());
    }
    #[test]
    fn packet_bounds() { assert!(parse(&[]).is_err()); assert!(parse(&[0;1233]).is_err()); }
}
