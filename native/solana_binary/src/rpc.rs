//! Experimental bounded transport only; kernel namespace/quota remain authoritative.
use pyo3::prelude::*;
use pyo3::exceptions::{PyConnectionError, PyRuntimeError, PyValueError};
use futures_util::{SinkExt, StreamExt};
use std::{sync::Arc, time::Duration};
use tokio::sync::{Mutex, Semaphore};
use tokio_tungstenite::{connect_async_with_config, MaybeTlsStream, WebSocketStream};
use tokio_tungstenite::tungstenite::{Message, protocol::WebSocketConfig};

fn bad(message: &str) -> PyErr { PyValueError::new_err(message.to_string()) }
fn offline() -> PyErr { PyConnectionError::new_err("native transport unavailable; endpoint omitted") }

fn boundary(inode: u64) -> PyResult<()> {
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::MetadataExt;
        if inode == 0 || std::fs::metadata("/proc/self/ns/net").map_err(|_| offline())?.ino() != inode {
            return Err(bad("approved network namespace mismatch"));
        }
        Ok(())
    }
    #[cfg(not(target_os = "linux"))]
    { let _ = inode; Err(bad("native network requires approved Linux kernel enforcement")) }
}

fn url(value: &str, websocket: bool, loopback: bool) -> PyResult<reqwest::Url> {
    if value.len()>4096 { return Err(bad("endpoint too long")); }
    let parsed = reqwest::Url::parse(value).map_err(|_| bad("invalid endpoint"))?;
    let secure = if websocket { "wss" } else { "https" };
    let local = if websocket { "ws" } else { "http" };
    if parsed.host_str().is_some_and(|s|s=="pump.fun" || s.ends_with(".pump.fun")) {
        return Err(bad("unreviewed web collector denied"));
    }
    let literal_loopback = parsed.host_str().and_then(|s| s.parse::<std::net::IpAddr>().ok()).is_some_and(|a| a.is_loopback());
    if parsed.host_str().is_none() || !parsed.username().is_empty() || parsed.password().is_some() || parsed.fragment().is_some()
        || !(parsed.scheme()==secure || loopback && literal_loopback && parsed.scheme()==local) {
        return Err(bad("TLS or opted-in literal loopback required"));
    }
    Ok(parsed)
}

fn request(method: &str, params: &str) -> PyResult<String> {
    if !["getGenesisHash","getSlot","getBlocks","getBlock","getAccountInfo","getBlockTime","getSignaturesForAddress","getTransaction"].contains(&method) {
        return Err(bad("read-only method allowlist violation"));
    }
    if params.len()>65536 { return Err(bad("request too large")); }
    let params: serde_json::Value = serde_json::from_str(params).map_err(|_| bad("invalid params"))?;
    if !params.is_array() { return Err(bad("params must be array")); }
    Ok(serde_json::json!({"jsonrpc":"2.0","id":1,"method":method,"params":params}).to_string())
}

type HttpReply = (u16,String,String);
#[pyclass]
struct NativeHttp {
    client: std::sync::Mutex<Option<reqwest::Client>>, endpoint: reqwest::Url,
    inode:u64, busy:Arc<Semaphore>, stop:tokio::sync::watch::Sender<bool>,
}
impl NativeHttp {
    fn operation(&self,method:&str,params:&str)->PyResult<impl std::future::Future<Output=PyResult<HttpReply>>+Send+'static> {
        boundary(self.inode)?;
        let body=request(method,params)?;
        let client=self.client.lock().map_err(|_|offline())?.as_ref().ok_or_else(offline)?.clone();
        let endpoint=self.endpoint.clone(); let mut stop=self.stop.subscribe();
        if *stop.borrow() {return Err(offline());}
        let permit=self.busy.clone().try_acquire_owned().map_err(|_|PyRuntimeError::new_err("HTTP backpressure: one request already active"))?;
        Ok(async move {
            let _permit=permit;
            tokio::select! {
                biased;
                _=stop.changed()=>Err(offline()),
                result=async {
                    let mut response=client.post(endpoint).header("Content-Type","application/json")
                        .header("Accept-Encoding","identity").body(body).send().await.map_err(|_|offline())?;
                    let status=response.status().as_u16();
                    let retry=response.headers().get("retry-after").and_then(|v|v.to_str().ok()).unwrap_or("").chars().take(256).collect::<String>();
                    if response.content_length().is_some_and(|n|n>64_000_000) {return Err(bad("oversized response"));}
                    let encoding=response.headers().get("content-encoding").and_then(|v|v.to_str().ok()).unwrap_or("identity");
                    if encoding!="identity" {return Err(bad("native response requires identity encoding"));}
                    let mut bytes=Vec::new();
                    while let Some(chunk)=response.chunk().await.map_err(|_|offline())? {
                        if bytes.len()+chunk.len()>64_000_000 {return Err(bad("oversized response"));}
                        bytes.extend_from_slice(&chunk);
                    }
                    Ok((status,retry,String::from_utf8(bytes).map_err(|_|bad("invalid response UTF8"))?))
                }=>result,
            }
        })
    }
}
#[pymethods]
impl NativeHttp {
    #[new]
    fn new(endpoint:&str,namespace_inode:u64,allow_loopback:bool)->PyResult<Self> {
        boundary(namespace_inode)?;
        let endpoint=url(endpoint,false,allow_loopback)?;
        // Bind pooled connections/timers to the one persistent Tokio runtime,
        // never to an ephemeral Python asyncio loop or caller's runtime.
        let _runtime=pyo3_async_runtimes::tokio::get_runtime().enter();
        let client=reqwest::Client::builder().redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30)).connect_timeout(Duration::from_secs(15))
            .pool_max_idle_per_host(1).pool_idle_timeout(Duration::from_secs(30)).no_proxy()
            .build().map_err(|_|offline())?;
        let (stop,_)=tokio::sync::watch::channel(false);
        Ok(Self {client:std::sync::Mutex::new(Some(client)),endpoint,inode:namespace_inode,busy:Arc::new(Semaphore::new(1)),stop})
    }
    fn request<'py>(&self,py:Python<'py>,method:&str,params:&str)->PyResult<Bound<'py,PyAny>> {
        pyo3_async_runtimes::tokio::future_into_py(py,self.operation(method,params)?)
    }
    fn request_sync(&self,py:Python<'_>,method:&str,params:&str)->PyResult<HttpReply> {
        if tokio::runtime::Handle::try_current().is_ok() {return Err(PyRuntimeError::new_err("blocking RPC inside Tokio is forbidden"));}
        let operation=self.operation(method,params)?;
        py.allow_threads(move ||pyo3_async_runtimes::tokio::get_runtime().block_on(operation))
    }
    fn close(&self)->PyResult<()> {
        self.stop.send_replace(true);
        self.client.lock().map_err(|_|offline())?.take();
        Ok(())
    }
}

type Socket = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;
#[pyclass]
struct NativeSocket {
    sink: Arc<Mutex<Option<futures_util::stream::SplitSink<Socket,Message>>>>,
    stream: Arc<Mutex<Option<futures_util::stream::SplitStream<Socket>>>>,
    send_busy: Arc<Semaphore>, recv_busy: Arc<Semaphore>,
    stop: tokio::sync::watch::Sender<bool>, inode: u64,
}
#[pymethods]
impl NativeSocket {
    fn send<'py>(&self, py: Python<'py>, text: String) -> PyResult<Bound<'py, PyAny>> {
        boundary(self.inode)?;
        if text.len()>65536 { return Err(bad("subscription too large")); }
        let value:serde_json::Value=serde_json::from_str(&text).map_err(|_|bad("invalid subscription"))?;
        if value["jsonrpc"]!="2.0" || value["method"]!="logsSubscribe" { return Err(bad("only logsSubscribe is supported")); }
        let sink=self.sink.clone(); let mut stop=self.stop.subscribe();
        let permit=self.send_busy.clone().try_acquire_owned().map_err(|_|PyRuntimeError::new_err("WS send backpressure"))?;
        pyo3_async_runtimes::tokio::future_into_py(py,async move {
            let _permit=permit;
            if *stop.borrow() { return Err(offline()); }
            tokio::select! {
                biased;
                _=stop.changed()=>Err(offline()),
                result=tokio::time::timeout(Duration::from_secs(15),async {
                    let mut guard=sink.lock().await;
                    guard.as_mut().ok_or_else(offline)?.send(Message::Text(text.into())).await.map_err(|_|offline())
                })=>result.map_err(|_|offline())?,
            }
        })
    }
    fn recv<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        boundary(self.inode)?;
        let stream=self.stream.clone(); let sink=self.sink.clone(); let mut stop=self.stop.subscribe();
        let permit=self.recv_busy.clone().try_acquire_owned().map_err(|_|PyRuntimeError::new_err("WS receive backpressure"))?;
        pyo3_async_runtimes::tokio::future_into_py(py,async move {
            let _permit=permit;
            if *stop.borrow() { return Err(offline()); }
            tokio::select! {
                biased;
                _=stop.changed()=>Err(offline()),
                result=async {
                    let mut guard=stream.lock().await;
                    let ws=guard.as_mut().ok_or_else(offline)?;
                    let mut controls=0;
                    let mut control_window=tokio::time::Instant::now();
                    loop {
                        let message=match tokio::time::timeout(Duration::from_secs(20),ws.next()).await {
                            Ok(message)=>message,
                            Err(_)=>{
                                controls=0;
                                let mut output=sink.lock().await;
                                tokio::time::timeout(Duration::from_secs(10),output.as_mut().ok_or_else(offline)?.send(Message::Ping(b"solana-observer".to_vec().into())))
                                    .await.map_err(|_|offline())?.map_err(|_|offline())?;
                                drop(output);
                                tokio::time::timeout(Duration::from_secs(10),ws.next()).await.map_err(|_|offline())?
                            },
                        };
                        match message {
                            Some(Ok(Message::Text(text)))=>return Ok(text.to_string()),
                            Some(Ok(Message::Ping(data)))=>{
                                let mut output=sink.lock().await;
                                tokio::time::timeout(Duration::from_secs(10),output.as_mut().ok_or_else(offline)?.send(Message::Pong(data)))
                                    .await.map_err(|_|offline())?.map_err(|_|offline())?;
                            },
                            Some(Ok(Message::Pong(_)))=>(),
                            _=>return Err(offline()),
                        }
                        if control_window.elapsed()>=Duration::from_secs(1) {
                            controls=0;control_window=tokio::time::Instant::now();
                        }
                        controls+=1;
                        if controls>16 {return Err(bad("excessive websocket control frames"));}
                    }
                }=>result,
            }
        })
    }
    fn close<'py>(&self,py:Python<'py>)->PyResult<Bound<'py,PyAny>> {
        // Signal before constructing the awaitable: canceling close cannot undo it.
        self.stop.send_replace(true);
        let sink=self.sink.clone(); let stream=self.stream.clone();
        pyo3_async_runtimes::tokio::future_into_py(py,async move {
            sink.lock().await.take(); stream.lock().await.take(); Ok(())
        })
    }
}

#[pyfunction]
fn connect_ws<'py>(py: Python<'py>, endpoint: &str, namespace_inode: u64, allow_loopback: bool) -> PyResult<Bound<'py, PyAny>> {
    boundary(namespace_inode)?;
    let endpoint=url(endpoint,true,allow_loopback)?.to_string();
    pyo3_async_runtimes::tokio::future_into_py(py,async move {
        let mut config=WebSocketConfig::default();
        config.max_message_size=Some(2_000_000); config.max_frame_size=Some(2_000_000);
        config.write_buffer_size=65536; config.max_write_buffer_size=262144;
        let (ws,_)=tokio::time::timeout(Duration::from_secs(15),connect_async_with_config(endpoint,Some(config),true)).await.map_err(|_| offline())?.map_err(|error| match error {
            tokio_tungstenite::tungstenite::Error::Http(response) => {
                let retry=response.headers().get("retry-after").and_then(|v|v.to_str().ok()).unwrap_or("");
                bad(&serde_json::json!({"ws_http_status":response.status().as_u16(),"retry_after":retry.chars().take(256).collect::<String>()}).to_string())
            },
            _ => offline(),
        })?;
        let (sink,stream)=ws.split();
        let (stop,_)=tokio::sync::watch::channel(false);
        Python::with_gil(|py|Py::new(py,NativeSocket {
            sink:Arc::new(Mutex::new(Some(sink))),stream:Arc::new(Mutex::new(Some(stream))),
            send_busy:Arc::new(Semaphore::new(1)),recv_busy:Arc::new(Semaphore::new(1)),stop,inode:namespace_inode,
        }))
    })
}

/// Offline replay summary, not market events or finalized verification.
#[pyfunction]
fn replay_binary_response(text: &str) -> PyResult<Vec<(usize,String,Option<bool>,Option<i16>)>> {
    use base64::Engine;
    if text.len()>64_000_000 { return Err(bad("oversized replay")); }
    let value:serde_json::Value=serde_json::from_str(text).map_err(|_| bad("invalid replay JSON"))?;
    if value["jsonrpc"]!="2.0" || value["id"].as_u64()!=Some(1) || value.get("error").is_some() {
        return Err(bad("invalid replay envelope"));
    }
    let result=value.get("result").ok_or_else(||bad("missing replay result"))?;
    let items:Vec<&serde_json::Value>=if let Some(items)=result["transactions"].as_array() {
        if items.len()>100_000 { return Err(bad("too many replay transactions")); }
        items.iter().collect()
    } else if result.get("transaction").is_some() { vec![result] }
    else { return Err(bad("no replay transactions")); };
    let mut rows=Vec::with_capacity(items.len());
    for (index,item) in items.iter().enumerate() {
        if !item.is_object() { rows.push((index,"quarantined".into(),None,None)); continue; }
        let tx=&item["transaction"];
        if tx.as_array().map(|v|v.len())!=Some(2) || tx.get(1).and_then(|v|v.as_str())!=Some("base64") {
            rows.push((index,"unsupported_json".into(),None,None)); continue;
        }
        let raw=tx.get(0).and_then(|v|v.as_str()).filter(|s|s.len()<=1644)
            .and_then(|s|base64::engine::general_purpose::STANDARD.decode(s).ok());
        let parsed=raw.as_ref().and_then(|r|super::parse(r).ok());
        let err=item.get("meta").and_then(|m|m.get("err"));
        if let (Some(parsed),Some(err))=(parsed,err) {
            if err.is_null() || err.is_object() || err.is_string() {
                rows.push((index,"parsed_unverified".into(),Some(err.is_null()),Some(parsed.0))); continue;
            }
        }
        rows.push((index,"quarantined".into(),None,None));
    }
    Ok(rows)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let mut runtime=tokio::runtime::Builder::new_multi_thread();
    runtime.worker_threads(2).max_blocking_threads(2).thread_stack_size(512*1024).enable_all();
    pyo3_async_runtimes::tokio::init(runtime);
    module.add_function(wrap_pyfunction!(replay_binary_response,module)?)?;
    module.add_class::<super::borsh::NativeBorshSchema>()?;
    module.add_class::<NativeHttp>()?;
    module.add_function(wrap_pyfunction!(connect_ws,module)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn read_only_requests() {
        assert!(request("sendTransaction","[]").is_err());
        assert!(request("getSlot","{}").is_err());
        let value:serde_json::Value=serde_json::from_str(&request("getSlot","[]").unwrap()).unwrap();
        assert_eq!(value["id"],1);
    }
    #[test] fn backpressure_is_bounded() {
        let semaphore=Arc::new(Semaphore::new(1));
        let permit=semaphore.clone().try_acquire_owned().unwrap();
        assert!(semaphore.clone().try_acquire_owned().is_err());
        drop(permit);
        assert!(semaphore.try_acquire_owned().is_ok());
    }
    #[test] fn independent_directions_and_persistent_shutdown() {
        let send=Arc::new(Semaphore::new(1)); let recv=Arc::new(Semaphore::new(1));
        let sending=send.clone().try_acquire_owned().unwrap();
        let receiving=recv.clone().try_acquire_owned().unwrap();
        assert!(send.clone().try_acquire_owned().is_err());
        assert!(recv.clone().try_acquire_owned().is_err());
        let (stop,_)=tokio::sync::watch::channel(false);
        stop.send_replace(true);
        assert!(*stop.subscribe().borrow());
        drop((sending,receiving));
        assert!(send.try_acquire_owned().is_ok());
        assert!(recv.try_acquire_owned().is_ok());
    }
    #[test] fn endpoints() {
        assert!(url("http://example.org",false,true).is_err());
        assert!(url("http://127.0.0.1:8899",false,true).is_ok());
        assert!(url("https://user:secret@example.org",false,false).is_err());
    }
}
