import { useState } from "react";
import type { FormEvent } from "react";
import { acceptCandidate, discoveryStatus, listCandidates, rejectCandidate, runDiscovery } from "../api";
import type { DiscoveryStatus } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, formatIso, useAsync } from "../components";

type Adapter = "rss" | "html";

const STATUS_LABEL: Record<string, string> = {
  new: "новый",
  accepted: "принят",
  rejected: "отклонён",
};

function statusTone(status: string): "info" | "ok" | "neutral" {
  if (status === "accepted") return "ok";
  if (status === "rejected") return "neutral";
  return "info";
}

export default function Discovery() {
  const [adapter, setAdapter] = useState<Adapter>("rss");
  const [url, setUrl] = useState("");
  const [filter, setFilter] = useState<string>("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, loading, error: listError } = useAsync(
    async () => {
      const [page, status] = await Promise.all([listCandidates(filter || undefined), discoveryStatus()]);
      return { page, status };
    },
    [filter, reload],
  );

  const run = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await runDiscovery(adapter, url.trim(), 50);
      setMessage(
        `Найдено ${result.items}: новых ${result.created}, уже известных ${result.updated}.`,
      );
      setReload((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Поиск не удался");
    } finally {
      setBusy(false);
    }
  };

  const accept = async (id: number, title: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const source = await acceptCandidate(id, title ?? undefined);
      setMessage(`Источник создан: ${source.name}`);
      setReload((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось принять кандидата");
    } finally {
      setBusy(false);
    }
  };

  const reject = async (id: number) => {
    setBusy(true);
    setError(null);
    try {
      await rejectCandidate(id);
      setMessage("Кандидат отклонён.");
      setReload((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отклонить кандидата");
    } finally {
      setBusy(false);
    }
  };

  const status: DiscoveryStatus | null = data?.status ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {error && <ErrorBanner message={error} />}

      <Card title="Поиск источников">
        <form onSubmit={run} className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
          <div className="field" style={{ flex: "0 0 150px", marginBottom: 0 }}>
            <label htmlFor="discovery-adapter">Адаптер</label>
            <select
              id="discovery-adapter"
              className="select"
              value={adapter}
              onChange={(event) => setAdapter(event.target.value as Adapter)}
            >
              <option value="rss">RSS / Atom</option>
              <option value="html">HTML-страница</option>
            </select>
          </div>
          <div className="field" style={{ flex: "1 1 320px", marginBottom: 0 }}>
            <label htmlFor="discovery-url">Ссылка на фид или тему форума</label>
            <input
              id="discovery-url"
              className="input"
              placeholder="https://forum.example/feed.xml"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              required
            />
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Поиск…" : "Найти"}
          </button>
        </form>
        {message && <p className="tiny" style={{ marginBottom: 0 }}>{message}</p>}
        <p className="tiny" style={{ marginBottom: 0 }}>
          Найденные ссылки попадают в очередь и не подключаются автоматически. Ссылки с учётными
          данными показываются только в замаскированном виде.
        </p>
      </Card>

      <Card
        title={`Кандидаты${data ? ` · ${data.page.total}` : ""}`}
        actions={
          <div className="row">
            {[
              ["", "все"],
              ["new", "новые"],
              ["accepted", "принятые"],
              ["rejected", "отклонённые"],
            ].map(([value, label]) => (
              <button
                key={value || "all"}
                className={`btn btn-sm${filter === value ? " btn-primary" : ""}`}
                onClick={() => setFilter(value)}
                type="button"
              >
                {label}
                {status && value === "new" ? ` ${status.new}` : ""}
              </button>
            ))}
          </div>
        }
        bodyless
      >
        {loading && <Loading />}
        {listError && (
          <div className="card-body">
            <ErrorBanner message={listError} />
          </div>
        )}
        {data && data.page.items.length === 0 && (
          <div className="empty">Кандидатов нет. Запустите поиск выше.</div>
        )}
        {data && data.page.items.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Тип</th>
                <th>Ссылка</th>
                <th>Заголовок</th>
                <th>Автор</th>
                <th>Найдено</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.page.items.map((candidate) => (
                <tr key={candidate.id}>
                  <td>
                    <StatusPill tone={candidate.has_credentials ? "warn" : "neutral"}>
                      {candidate.kind}
                    </StatusPill>
                  </td>
                  <td className="mono muted">{candidate.url_masked}</td>
                  <td>{candidate.title ?? "—"}</td>
                  <td className="muted">{candidate.author ?? "—"}</td>
                  <td className="muted">{formatIso(candidate.created_at)}</td>
                  <td>
                    <StatusPill tone={statusTone(candidate.status)}>
                      {STATUS_LABEL[candidate.status] ?? candidate.status}
                    </StatusPill>
                  </td>
                  <td>
                    {candidate.status === "new" ? (
                      <div className="row">
                        <button
                          className="btn btn-sm btn-primary"
                          onClick={() => void accept(candidate.id, candidate.title)}
                          disabled={busy}
                        >
                          Принять
                        </button>
                        <button
                          className="btn btn-sm"
                          onClick={() => void reject(candidate.id)}
                          disabled={busy}
                        >
                          Отклонить
                        </button>
                      </div>
                    ) : (
                      <span className="tiny">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
