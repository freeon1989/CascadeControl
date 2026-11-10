import { useEffect, useState } from "react";
import { api } from "../api";
import { saveDownload } from "../saveFile";
import { Banner, Page } from "./Overview";

type Job = { id: string; kind: string; status: string; created_at: string; lines: string[] };

const titles: Record<string, string> = {
  install: "Установка",
  precheck: "Проверка",
  remove: "Удаление",
  update: "Обновление",
  alert: "Предупреждение",
  "dns-log": "Журнал сайтов",
  ports: "Порты",
};

export function JournalPage() {
  const [rows, setRows] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    api<Job[]>("/api/journal").then(setRows).catch((err: Error) => setError(err.message));
  }, []);

  return (
    <Page title="Журнал" subtitle="Ключи и пароли в эти строки не пишутся">
      {error && <Banner text={error} />}
      {!rows && <Banner text="Загрузка журнала…" />}
      {rows && rows.length === 0 && <Banner text="Журнал пуст. Проверка и установка сервера появятся здесь." />}
      {notice && <Banner text={notice} />}
      <div className="flex flex-col gap-3">
        {rows?.map((job) => (
          <article key={job.id} className="card p-4">
            <div className="flex items-center justify-between text-sm">
              <div className="font-medium">{titles[job.kind] || job.kind}</div>
              <div className={job.status === "error" ? "text-rose" : "text-ok"}>{job.status}</div>
            </div>
            <div className="text-xs text-mist">{job.created_at}</div>
            <pre className="mt-2 whitespace-pre-wrap text-xs text-mist">{job.lines.join("\n")}</pre>
            <button
              className="mt-2 text-xs text-teal"
              onClick={() => {
                const body = job.lines.join("\n");
                void saveDownload(`journal-${job.kind}.txt`, body).then((saved) => {
                  if (saved) setNotice(`Сохранено: ${saved}`);
                });
              }}
            >
              Скачать
            </button>
          </article>
        ))}
      </div>
    </Page>
  );
}
