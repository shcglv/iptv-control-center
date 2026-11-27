import { useState } from "react";
import { diagnoseChannel, listChannels } from "../api";
import type { Diagnose } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, useAsync } from "../components";

const PAGE_SIZE = 25;

export default function Channels() {
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [diagnosis, setDiagnosis] = useState<Diagnose | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const [diagError, setDiagError] = useState<string | null>(null);

  const { data, error, loading } = useAsync(() => listChannels(search, PAGE_SIZE, offset), [search, offset]);

  const applySearch = (event: React.FormEvent) => {
    event.preventDefault();
    setOffset(0);
    setSearch(draft.trim());
  };

  const diagnose = async (key: string) => {
    setDiagBusy(true);
    setDiagError(null);
    try {
      setDiagnosis(await diagnoseChannel(key));
    } catch (err) {
      setDiagError(err instanceof Error ? err.message : "Не удалось получить диагностику");
    } finally {
      setDiagBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        title={`Каналы${data ? ` · ${data.total.toLocaleString("ru-RU")}` : ""}`}
        actions={
          <form className="row" onSubmit={applySearch}>
            <input
              className="input"
              placeholder="Поиск по названию"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              style={{ width: 240 }}
            />
            <button className="btn btn-sm" type="submit">
              Найти
            </button>
          </form>
        }
        bodyless
      >
        {loading && <Loading />}
        {error && <div className="card-body"><ErrorBanner message={error} /></div>}
        {data && data.items.length === 0 && <div className="empty">Каналов нет. Добавьте источник и импортируйте плейлист.</div>}
        {data && data.items.length > 0 && (
          <table>
            <thead>
              <tr>
                <th style={{ width: 60 }}>№</th>
                <th>Название</th>
                <th>Группа</th>
                <th>tvg-id</th>
                <th style={{ width: 90 }}>Источники</th>
                <th>Ключ</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((channel) => (
                <tr key={channel.id}>
                  <td className="num">{channel.number ?? "—"}</td>
                  <td>{channel.name}</td>
                  <td className="muted">{channel.group ?? "—"}</td>
                  <td className="mono muted">{channel.tvg_id ?? "—"}</td>
                  <td className="num">{channel.sources}</td>
                  <td className="mono muted">{channel.public_key}</td>
                  <td>
                    <button
                      className="btn btn-sm"
                      aria-label={`Диагностика ${channel.name}`}
                      onClick={() => void diagnose(channel.public_key)}
                      disabled={diagBusy}
                    >
                      Почему?
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && data.total > PAGE_SIZE && (
          <div className="card-body row">
            <button className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Назад
            </button>
            <span className="tiny">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} из {data.total}
            </span>
            <button
              className="btn btn-sm"
              disabled={offset + PAGE_SIZE >= data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Вперёд
            </button>
          </div>
        )}
      </Card>

      {(diagnosis || diagError) && (
        <Card
          title={diagnosis ? `Почему так: ${diagnosis.name}` : "Диагностика"}
          actions={
            <button
              className="btn btn-sm"
              onClick={() => {
                setDiagnosis(null);
                setDiagError(null);
              }}
            >
              Закрыть
            </button>
          }
          bodyless
        >
          {diagError && (
            <div className="card-body">
              <ErrorBanner message={diagError} />
            </div>
          )}
          {diagnosis && (
            <>
              <div className="card-body" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                {diagnosis.hidden ? (
                  <StatusPill tone="err">скрыт{diagnosis.blocked_by ? ` (${diagnosis.blocked_by})` : ""}</StatusPill>
                ) : (
                  <StatusPill tone="ok">виден</StatusPill>
                )}
                <span className="tiny">tvg-id: {diagnosis.tvg_id ?? "—"}</span>
                <span className="tiny">каталог: {diagnosis.catalog_id ?? "—"}</span>
                <span className="tiny">
                  выбранный поток: {diagnosis.active_stream_id ?? "ещё не выбирался"}
                </span>
                <span className="tiny">
                  профили: {diagnosis.profiles.length === 0
                    ? "нет"
                    : diagnosis.profiles
                        .map((profile) => `${profile.name}${profile.visible ? "" : " (скрыт)"}`)
                        .join(", ")}
                </span>
              </div>
              {diagnosis.streams.length === 0 ? (
                <div className="empty">У канала нет источников.</div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Поток</th>
                      <th>Источник</th>
                      <th>Контейнер</th>
                      <th style={{ width: 80 }}>Score</th>
                      <th style={{ width: 90 }}>Uptime</th>
                      <th>Почему не используется</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diagnosis.streams.map((stream) => (
                      <tr key={stream.stream_id}>
                        <td className="num">{stream.stream_id}</td>
                        <td>{stream.source}</td>
                        <td className="mono">{stream.container}</td>
                        <td className="num">{stream.score.toFixed(1)}</td>
                        <td className="num">
                        {Math.round(stream.uptime_ratio * 100)}%
                        {stream.uptime_ratio === 0 && stream.last_error_class ? (
                          <div className="muted" style={{ fontSize: 11 }}>
                            {stream.last_error_class}
                          </div>
                        ) : null}
                      </td>
                        <td className="muted">
                          {stream.reasons.length ? stream.reasons.join("; ") : "подходит"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="card-body tiny">
                Разрешение по контейнерам: ts → {diagnosis.resolution.ts ?? "—"}, m3u8 →{" "}
                {diagnosis.resolution.m3u8 ?? "—"}, mpd → {diagnosis.resolution.mpd ?? "—"}
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
