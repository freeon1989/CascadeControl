type SaveApi = {
  save_file: (name: string, text: string) => Promise<string> | string;
};

function bridge(): SaveApi | null {
  const host = window as Window & { pywebview?: { api?: SaveApi } };
  if (typeof host.pywebview?.api?.save_file === "function") return host.pywebview.api;
  return null;
}

function browserDownload(name: string, text: string) {
  const link = document.createElement("a");
  let url = text;
  if (!text.startsWith("data:")) {
    url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  }
  link.href = url;
  link.download = name;
  link.click();
  if (!text.startsWith("data:")) URL.revokeObjectURL(url);
}

/** В окне панели открывает диалог сохранения. В браузере остаётся обычная загрузка. */
export async function saveDownload(name: string, text: string): Promise<string> {
  const api = bridge();
  if (!api) {
    browserDownload(name, text);
    return "";
  }
  const saved = await api.save_file(name, text);
  return saved || "";
}
