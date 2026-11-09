// The text editor: Monaco when it was fetched (scripts/fetch_ui.py), a plain text area when not.
// Both give the same small interface, so the rest of the page does not care which one it has.

export const PAUSE_MS = 500; // a pause this long after the last key checks the text (US-26)

const WORKER = "vendor/monaco/vs/assets/editor.worker.js";

function loadMonaco() {
  return new Promise((resolve, reject) => {
    if (typeof window.require !== "function" || !window.require.config) {
      reject(new Error("the editor was not fetched"));
      return;
    }
    const timer = setTimeout(() => reject(new Error("the editor did not load")), 15000);
    window.MonacoEnvironment = {
      getWorker: () => new Worker(new URL(WORKER, document.baseURI), { type: "module" }),
    };
    window.require.config({ paths: { vs: "vendor/monaco/vs" } });
    window.require(
      ["vs/editor/editor.main"],
      () => {
        clearTimeout(timer);
        resolve(window.monaco);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

const LEVELS = (monaco) => ({
  error: monaco.MarkerSeverity.Error,
  warning: monaco.MarkerSeverity.Warning,
  info: monaco.MarkerSeverity.Info,
});

function monacoEditor(monaco, host) {
  const editor = monaco.editor.create(host, {
    value: "",
    language: "yaml",
    automaticLayout: true,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    fontSize: 13,
    tabSize: 2,
    insertSpaces: true,
    wordBasedSuggestions: "off", // the only suggestions are the server's: what is available here
    quickSuggestions: { other: false, comments: false, strings: false },
  });
  const model = editor.getModel();
  const levels = LEVELS(monaco);
  return {
    kind: "monaco",
    monaco,
    editor,
    model,
    getText: () => model.getValue(),
    setText: (text) => model.setValue(text),
    onChange: (callback) => model.onDidChangeContent(() => callback()),
    // The server gives a line and a column and no end; the mark covers the word there.
    setMarkers(items) {
      const lines = model.getLineCount();
      const marks = items.map((d) => {
        const line = Math.min(Math.max(d.line, 1), lines);
        const column = Math.min(Math.max(d.column, 1), model.getLineMaxColumn(line));
        const word = model.getWordAtPosition({ lineNumber: line, column });
        const end = word && word.endColumn > column ? word.endColumn : column + 1;
        return {
          startLineNumber: line,
          startColumn: column,
          endLineNumber: line,
          endColumn: end,
          message: `${d.code}: ${d.message}`,
          severity: levels[d.severity] ?? monaco.MarkerSeverity.Info,
          source: "flowstat",
          code: d.code,
        };
      });
      monaco.editor.setModelMarkers(model, "flowstat", marks);
    },
    // ask(text, line, column) gives the answer of POST /complete (or null); it is called when the
    // person types "${", ".", or "[" and on the keys of the suggest command.
    registerCompletion(ask) {
      const kinds = {
        input: monaco.languages.CompletionItemKind.Variable,
        output: monaco.languages.CompletionItemKind.Field,
      };
      monaco.languages.registerCompletionItemProvider("yaml", {
        triggerCharacters: ["{", ".", "["],
        async provideCompletionItems(target, position) {
          if (target !== model) return { suggestions: [] };
          const found = await ask(target.getValue(), position.lineNumber, position.column);
          if (!found || found.items.length === 0) return { suggestions: [] };
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: found.from_column,
            endColumn: position.column,
          };
          return {
            suggestions: found.items.map((item, n) => ({
              label: item.label,
              kind: kinds[item.kind],
              detail: item.detail,
              insertText: found.closed ? item.label : `${item.label}}`,
              filterText: item.label,
              sortText: String(n).padStart(4, "0"),
              range,
            })),
          };
        },
      });
    },
    reveal(line, column) {
      editor.revealLineInCenter(line);
      editor.setPosition({ lineNumber: line, column });
      editor.focus();
    },
  };
}

function plainEditor(plain, host) {
  host.hidden = true;
  plain.hidden = false;
  return {
    kind: "plain",
    getText: () => plain.value,
    setText: (text) => {
      plain.value = text;
      plain.dispatchEvent(new Event("input"));
    },
    onChange: (callback) => plain.addEventListener("input", callback),
    setMarkers() {
      /* a text area cannot mark a position; the list of problems shows them */
    },
    reveal(line, column) {
      const lines = plain.value.split("\n");
      let offset = 0;
      for (let i = 0; i < line - 1 && i < lines.length; i++) offset += lines[i].length + 1;
      plain.focus();
      plain.setSelectionRange(offset + column - 1, offset + column - 1);
    },
  };
}

export async function createEditor(host, plain, forcePlain) {
  if (!forcePlain) {
    try {
      return monacoEditor(await loadMonaco(), host);
    } catch {
      /* fall through to the text area */
    }
  }
  return plainEditor(plain, host);
}
