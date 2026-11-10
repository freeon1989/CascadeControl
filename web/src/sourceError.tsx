import { useState } from "react";

export function SourceError({ raw }: { raw: string }) {
  const [open, setOpen] = useState(false);
  let message = raw;
  let detail = "";
  try {
    const parsed = JSON.parse(raw) as { message?: string; detail?: string };
    if (parsed && typeof parsed.message === "string") {
      message = parsed.message;
      detail = parsed.detail || "";
    }
  } catch {
    /* старые ошибки хранятся обычным текстом */
  }
  return (
    <div className="mt-1 text-xs text-amber">
      <div>{message}</div>
      {detail && (
        <button type="button" className="mt-1 text-mist underline" onClick={() => setOpen((value) => !value)}>
          {open ? "Скрыть подробности" : "Подробности"}
        </button>
      )}
      {open && <pre className="mt-1 whitespace-pre-wrap break-all text-[11px] text-mist">{detail}</pre>}
    </div>
  );
}
