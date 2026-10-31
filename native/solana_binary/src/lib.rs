//! Binary structure only: no execution, address loading, program decoding or trust.
#![forbid(unsafe_code)]
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type Instruction = (u8, Vec<u8>, Vec<u8>);
type Lookup = (Vec<u8>, Vec<u8>, Vec<u8>);
type Parsed = (i16, Vec<Vec<u8>>, (u8,u8,u8), Vec<Vec<u8>>, Vec<u8>, Vec<Instruction>, Vec<Lookup>);

struct Reader<'a> { raw: &'a [u8], offset: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, count: usize) -> Result<Vec<u8>, &'static str> {
        let end = self.offset.checked_add(count).ok_or("length overflow")?;
        let data = self.raw.get(self.offset..end).ok_or("truncated transaction")?;
        self.offset = end;
        Ok(data.to_vec())
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

fn parse(raw: &[u8]) -> Result<Parsed, &'static str> {
    if raw.is_empty() || raw.len() > 1232 { return Err("transaction size outside supported packet limit"); }
    let mut r = Reader { raw, offset: 0 };
    let count = r.short(19)?;
    let mut signatures = Vec::with_capacity(count);
    for _ in 0..count { signatures.push(r.take(64)?); }
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
    for _ in 0..count { keys.push(r.take(32)?); }
    let blockhash = r.take(32)?;
    let count = r.short(1232)?;
    let mut instructions = Vec::with_capacity(count.min(raw.len()/3));
    for _ in 0..count {
        let program = r.byte()?;
        if program as usize >= keys.len() { return Err("program index outside static keys"); }
        let count = r.short(256)?;
        let accounts = r.take(count)?;
        let count = r.short(1232)?;
        instructions.push((program, accounts, r.take(count)?));
    }
    let mut lookups = Vec::new();
    let mut loaded = 0usize;
    if version == 0 {
        let count = r.short(256)?;
        for _ in 0..count {
            let key = r.take(32)?;
            let count = r.short(256)?;
            let writable = r.take(count)?;
            let count = r.short(256)?;
            let readonly = r.take(count)?;
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
    parse(raw).map_err(PyValueError::new_err)
}

#[pymodule]
fn _solana_binary(module: &Bound<'_, PyModule>) -> PyResult<()> {
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
    fn packet_bounds() { assert!(parse(&[]).is_err()); assert!(parse(&[0;1233]).is_err()); }
}
