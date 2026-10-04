/// Accumulates raw bytes from a stream and yields complete NDJSON lines,
/// keeping any trailing partial line buffered across calls. Blank lines are dropped.
///
/// There is no line-length cap: `ans` and mirror `fb` events are single lines
/// of hundreds of KB. `scanned` remembers how far the buffered partial line has
/// already been searched for `\n`, so a big line arriving in many small reads
/// costs O(n) rather than rescanning the whole buffer on every chunk.
pub struct LineBuffer {
    buf: Vec<u8>,
    scanned: usize,
}

impl LineBuffer {
    pub fn new() -> Self {
        Self {
            buf: Vec::new(),
            scanned: 0,
        }
    }

    /// Push a chunk of bytes; return every complete line (without its trailing `\n`).
    pub fn push(&mut self, data: &[u8]) -> Vec<String> {
        self.buf.extend_from_slice(data);
        let mut lines = Vec::new();
        while let Some(off) = self.buf[self.scanned..].iter().position(|&b| b == b'\n') {
            let pos = self.scanned + off;
            self.scanned = 0;
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            let line = &line[..line.len() - 1]; // strip the trailing '\n'
            if line.is_empty() {
                continue;
            }
            lines.push(String::from_utf8_lossy(line).into_owned());
        }
        self.scanned = self.buf.len();
        lines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_complete_line() {
        let mut lb = LineBuffer::new();
        assert_eq!(lb.push(b"{\"t\":\"clear\"}\n"), vec!["{\"t\":\"clear\"}"]);
    }

    #[test]
    fn multiple_lines_in_one_chunk() {
        let mut lb = LineBuffer::new();
        assert_eq!(lb.push(b"a\nb\nc\n"), vec!["a", "b", "c"]);
    }

    #[test]
    fn partial_line_spans_two_chunks() {
        let mut lb = LineBuffer::new();
        assert!(lb.push(b"{\"t\":\"do").is_empty());
        assert_eq!(lb.push(b"wn\"}\n"), vec!["{\"t\":\"down\"}"]);
    }

    #[test]
    fn blank_lines_are_dropped() {
        let mut lb = LineBuffer::new();
        assert_eq!(lb.push(b"a\n\n\nb\n"), vec!["a", "b"]);
    }

    /// An `ans` event is one line of tens to hundreds of KB, arriving over many
    /// 4 KB reads. The splitter must reassemble it rather than cap or truncate.
    #[test]
    fn very_long_line_is_reassembled_across_many_chunks() {
        let mut lb = LineBuffer::new();
        let payload = "A".repeat(400_000);
        let line = format!("{{\"t\":\"ans\",\"png\":\"{payload}\"}}");
        let bytes = format!("{line}\n").into_bytes();
        let mut out = Vec::new();
        for chunk in bytes.chunks(4096) {
            out.extend(lb.push(chunk));
        }
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].len(), line.len());
        assert_eq!(out[0], line);
    }

    /// A mirror keyframe (`fb`, ~300 KB of base64 PNG) arrives in uneven reads,
    /// with the next small event glued onto the final chunk. Both must come out
    /// intact and in order.
    #[test]
    fn mirror_keyframe_line_split_unevenly_then_next_event() {
        let mut lb = LineBuffer::new();
        let b64: String = (0..300_000)
            .map(|i| b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"[i % 64] as char)
            .collect();
        let fb = format!(
            "{{\"t\":\"fb\",\"x\":0,\"y\":0,\"w\":1404,\"h\":1872,\"key\":1,\"png\":\"{b64}\"}}"
        );
        let next = r#"{"t":"fb","x":8,"y":16,"w":64,"h":32,"key":0,"png":"AAAA"}"#;
        let bytes = format!("{fb}\n{next}\n").into_bytes();
        let sizes = [1usize, 7, 4096, 13, 65536, 333, 2];
        let mut out = Vec::new();
        let mut i = 0;
        let mut k = 0;
        while i < bytes.len() {
            let n = sizes[k % sizes.len()].min(bytes.len() - i);
            out.extend(lb.push(&bytes[i..i + n]));
            i += n;
            k += 1;
        }
        assert_eq!(out.len(), 2);
        assert_eq!(out[0], fb);
        assert_eq!(out[1], next);
        let v: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        assert_eq!(v["png"].as_str().unwrap().len(), 300_000);
    }

    #[test]
    fn trailing_partial_is_retained_not_emitted() {
        let mut lb = LineBuffer::new();
        assert_eq!(lb.push(b"done\npart"), vec!["done"]);
        assert_eq!(lb.push(b"ial\n"), vec!["partial"]);
    }
}
