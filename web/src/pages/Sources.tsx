import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, createSource, importM3u, listSources } from "../api";
import type { ImportStats } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, formatIso, useAsync } from "../components";

export default function Sources() {
  const [reload, setReload] = useState(0);
  const { data, error, loading } = useAsync(listSources, [reload]);

  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [targetId, setTargetId] = useState<number | "">("");
  const [playlist, setPlaylist] = useState("");
  const [stats, setStats] = useState<ImportStats | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const addSource = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setCreateError(null);
    try {
      const created = await createSource(name.trim(), url.trim());
      setName("");
      setUrl("");
      setTargetId(created.id);
      setReload((value) => value + 1);
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Не удалось создать источник");
    } finally {
      setBusy(false);
    }
  };

  const runImport = async (event: FormEvent) => {
    event.preventDefault();
    if (targetId === "") return;
    setBusy(true);
    setImportError(null);
    setStats(null);
    try {
      const result = await importM3u(Number(targetId), playlist);
      setStats(result.stats);
      setPlaylist("");
      setReload((value) => value + 1);
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Импорт не удался");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title="Источники" bodyless>
        {loading && <Loading />}
        {error && <div className="card-body"><ErrorBanner message={error} /></div>}
        {data && data.length === 0 && <div className="empty">Источников нет — добавьте первый ниже.</div>}
        {data && data.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Название</th>
                <th>URL</th>
                <th>Потоки</th>
                <th>Статус</th>
                <th>Импорт</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((source) => (
                <tr key={source.id}>
                  <td>{source.name}</td>
                  <td className="mono muted">{source.url_masked || "—"}</td>
                  <td className="num">{source.stream_count}</td>
                  <td>
                    {source.last_status === "success" ? (
                      <StatusPill tone="ok">OK</StatusPill>
                    ) : source.last_status === "error" ? (
                      <StatusPill tone="err">Ошибка</StatusPill>
                    ) : (
                      <StatusPill tone="neutral">Не импортирован</StatusPill>
                    )}
                  </td>
                  <td className="muted">{formatIso(source.last_import_at)}</td>
                  <td>
                    <button className="btn btn-sm" onClick={() => setTargetId(source.id)}>
                      Импортировать
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Card title="Новый источник">
          {createError && <ErrorBanner message={createError} />}
          <form onSubmit={addSource}>
            <div className="field">
              <label htmlFor="source-name">Название</label>
              <input
                id="source-name"
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="source-url">URL плейлиста (необязательно)</label>
              <input
                id="source-url"
                className="input"
                placeholder="https://provider.example/list.m3u"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              Добавить
            </button>
          </form>
        </Card>

        <Card title="Импорт M3U">
          {importError && <ErrorBanner message={importError} />}
          <form onSubmit={runImport}>
            <div className="field">
              <label htmlFor="import-target">Источник</label>
              <select
                id="import-target"
                className="select"
                value={targetId}
                onChange={(event) => setTargetId(event.target.value === "" ? "" : Number(event.target.value))}
                required
              >
                <option value="">— выберите —</option>
                {(data ?? []).map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="import-body">Содержимое плейлиста</label>
              <textarea
                id="import-body"
                className="textarea"
                rows={7}
                placeholder="#EXTM3U…"
                value={playlist}
                onChange={(event) => setPlaylist(event.target.value)}
                required
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy || targetId === ""}>
              Импортировать
            </button>
            {stats && (
              <div className="tiny" style={{ marginTop: 10 }}>
                Разобрано {stats.parsed}, добавлено {stats.created}, обновлено {stats.updated}, каналов{" "}
                {stats.channels_created}, привязано {stats.channels_linked}, устарело {stats.stale}
                {stats.skipped > 0 ? `, пропущено ${stats.skipped}` : ""}
              </div>
            )}
          </form>
        </Card>
      </div>
    </div>
  );
}
