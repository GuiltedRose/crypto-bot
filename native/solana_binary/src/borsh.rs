//! Bounded pinned-IDL field extraction only. Parent CPI/authority/version checks
//! and market normalization stay in Python; fields alone are never trade proof.
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use serde_json::{Value,json};

fn error(s:&str)->PyErr { PyValueError::new_err(s.to_owned()) }
struct Reader<'a> { raw:&'a[u8], offset:usize, nodes:usize }
impl<'a> Reader<'a> {
    fn take(&mut self,n:usize)->PyResult<&'a[u8]> {
        let end=self.offset.checked_add(n).ok_or_else(||error("Borsh length overflow"))?;
        let raw=self.raw.get(self.offset..end).ok_or_else(||error("Truncated Borsh field"))?;
        self.offset=end; Ok(raw)
    }
    fn value(&mut self,kind:&Value,types:&Value,depth:usize)->PyResult<Value> {
        self.nodes+=1;
        if depth>32 || self.nodes>16384 { return Err(error("Borsh complexity limit")); }
        if let Some(kind)=kind.as_str() {
            return match kind {
                "pubkey"=>Ok(Value::String(base58(self.take(32)?))),
                "bool"=>match self.take(1)?[0] {0=>Ok(json!(false)),1=>Ok(json!(true)),_=>Err(error("Invalid Borsh bool"))},
                "string"=>{
                    let n=u32::from_le_bytes(self.take(4)?.try_into().map_err(|_|error("string size"))?) as usize;
                    if n>16384 { return Err(error("Oversized string")); }
                    Ok(json!(std::str::from_utf8(self.take(n)?).map_err(|_|error("Invalid UTF8"))?))
                },
                _=>{
                    let signed=kind.starts_with('i');
                    if !(signed || kind.starts_with('u')) { return Err(error("Unsupported Borsh type")); }
                    let bits=kind.get(1..).and_then(|s|s.parse::<usize>().ok()).ok_or_else(||error("Unsupported Borsh type"))?;
                    if ![8,16,32,64,128].contains(&bits) { return Err(error("Unsupported Borsh integer")); }
                    let raw=self.take(bits/8)?;
                    let mut bytes=[0u8;16]; bytes[..raw.len()].copy_from_slice(raw);
                    let number=if signed {
                        if raw[raw.len()-1]&128!=0 { bytes[raw.len()..].fill(255); }
                        i128::from_le_bytes(bytes).to_string()
                    } else {u128::from_le_bytes(bytes).to_string()};
                    // Typed marker preserves integers beyond JSON's u64 range.
                    Ok(json!({"$integer":number}))
                },
            };
        }
        if let Some(element)=kind.get("option") {
            return match self.take(1)?[0] {0=>Ok(Value::Null),1=>self.value(element,types,depth+1),_=>Err(error("Invalid Borsh option tag"))};
        }
        if let Some(name)=kind.get("defined").and_then(|x|x.get("name")).and_then(Value::as_str) {
            let definition=types.get(name).ok_or_else(||error("Unknown Borsh definition"))?;
            if definition["kind"]=="enum" {
                let tag=self.take(1)?[0] as usize;
                let variant=definition["variants"].get(tag).ok_or_else(||error("Invalid Borsh enum tag"))?;
                if variant["fields"].as_array().is_some_and(|v|!v.is_empty()) {return Err(error("Unreviewed enum payload"));}
                return Ok(variant["name"].clone());
            }
            let fields=definition.get("fields").and_then(Value::as_array).ok_or_else(||error("Unknown Borsh struct"))?;
            if fields.first().is_some_and(Value::is_string) {
                let mut values=Vec::new();
                for field in fields {values.push(self.value(field,types,depth+1)?);}
                return Ok(Value::Array(values));
            }
            return self.fields(fields,types,depth+1);
        }
        let (element,count)=if let Some(element)=kind.get("vec") {
            (element,u32::from_le_bytes(self.take(4)?.try_into().map_err(|_|error("vector size"))?) as usize)
        } else if let Some(array)=kind.get("array").and_then(Value::as_array) {
            if array.len()!=2 { return Err(error("Invalid Borsh array")); }
            (&array[0],array[1].as_u64().ok_or_else(||error("Invalid array size"))? as usize)
        } else {return Err(error("Unsupported Borsh type"));};
        if count>4096 {return Err(error("Oversized vector"));}
        let mut values=Vec::with_capacity(count);
        for _ in 0..count {values.push(self.value(element,types,depth+1)?);}
        Ok(Value::Array(values))
    }
    fn fields(&mut self,fields:&[Value],types:&Value,depth:usize)->PyResult<Value> {
        if fields.len()>256 { return Err(error("Too many Borsh fields")); }
        let mut result=serde_json::Map::new();
        for field in fields {
            let name=field["name"].as_str().ok_or_else(||error("Invalid field name"))?;
            if result.contains_key(name) {return Err(error("Duplicate field name"));}
            result.insert(name.to_owned(),self.value(&field["type"],types,depth+1)?);
        }
        Ok(Value::Object(result))
    }
}
fn base58(raw:&[u8])->String {
    const ABC:&[u8]=b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    let mut digits=vec![0u8];
    for byte in raw {
        let mut carry=*byte as u32;
        for digit in &mut digits { carry+=(*digit as u32)*256; *digit=(carry%58) as u8; carry/=58; }
        while carry>0 {digits.push((carry%58) as u8);carry/=58;}
    }
    let zeros=raw.iter().take_while(|b|**b==0).count();
    let mut result="1".repeat(zeros);
    if zeros!=raw.len() {for digit in digits.iter().rev() {result.push(ABC[*digit as usize] as char);}}
    result
}
#[pyclass]
pub struct NativeBorshSchema { fields:Vec<Value>,types:Value }
#[pymethods]
impl NativeBorshSchema {
    #[new]
    fn new(fields:&str,types:&str)->PyResult<Self> {
        if fields.len()>65536 || types.len()>262144 {return Err(error("Schema size limit"));}
        let fields:Vec<Value>=serde_json::from_str(fields).map_err(|_|error("Invalid fields schema"))?;
        let types:Value=serde_json::from_str(types).map_err(|_|error("Invalid type schema"))?;
        if fields.len()>256 || !types.is_object() {return Err(error("Invalid schema bounds"));}
        Ok(Self {fields,types})
    }
    fn decode(&self,raw:&[u8],offset:usize)->PyResult<(String,usize)> {
        if raw.len()>65536 || offset>raw.len() {return Err(error("Borsh input limit"));}
        let mut reader=Reader {raw,offset,nodes:0};
        let value=reader.fields(&self.fields,&self.types,0)?;
        let result=serde_json::to_string(&value).map_err(|_|error("Borsh output error"))?;
        if result.len()>1048576 {return Err(error("Borsh output limit"));}
        Ok((result,reader.offset))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn pubkeys() {assert_eq!(base58(&[0;32]),"1".repeat(32)); assert_eq!(base58(&[0,1]),"12");}
    #[test] fn bad_boolean() {
        let mut r=Reader {raw:&[2],offset:0,nodes:0};
        assert!(r.value(&json!("bool"),&json!({}),0).is_err());
    }
}
