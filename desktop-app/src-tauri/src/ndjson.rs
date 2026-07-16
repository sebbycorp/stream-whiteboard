/// Accumulates raw bytes from a stream and yields complete NDJSON lines,
/// keeping any trailing partial line buffered across calls. Blank lines are dropped.
pub struct LineBuffer {
    buf: Vec<u8>,
}

impl LineBuffer {
    pub fn new() -> Self {
        Self { buf: Vec::new() }
    }

    /// Push a chunk of bytes; return every complete line (without its trailing `\n`).
    pub fn push(&mut self, data: &[u8]) -> Vec<String> {
        self.buf.extend_from_slice(data);
        let mut lines = Vec::new();
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            let line = &line[..line.len() - 1]; // strip the trailing '\n'
            if line.is_empty() {
                continue;
            }
            lines.push(String::from_utf8_lossy(line).into_owned());
        }
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

    #[test]
    fn trailing_partial_is_retained_not_emitted() {
        let mut lb = LineBuffer::new();
        assert_eq!(lb.push(b"done\npart"), vec!["done"]);
        assert_eq!(lb.push(b"ial\n"), vec!["partial"]);
    }
}
