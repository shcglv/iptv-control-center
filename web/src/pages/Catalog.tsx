import { useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  addBlock,
  catalogStatus,
  deleteBlock,
  importCatalog,
  listBlocks,
  matchCatalog,
} from "../api";
import type { BlockKind, BlockRow, CatalogStatus } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, formatIso, useAsync } from "../components";

const KIND_LABEL: Record<BlockKind, string> = {
  catalog_id: "ID каталога",
  tvg_id: "tvg-id",
  name: "Название",
  domain: "Домен источника",
};

export default function Catalog() {
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [payload, setPayload] = useState("");
  const [kind, setKind] = useState<BlockKind>("catalog_id");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");

  const { data, loading, error: loadError } = useAsync(
    async () => {
      const [status, blocks] = await Promise.all([catalogStatus(), listBlocks()]);
      return { status, blocks };
    },
    [reload],
  );

  const status: CatalogStatus | null = data?.status ?? null;
  const blocks: BlockRow[] = data?.blocks ?? [];

  const refresh = () => setReload((current) => current + 1);

  const loadFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setPayload(await file.text());
    setMessage(`Загружен файл ${file.name}`);
  };

  const runImport = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const parsed: unknown = JSON.parse(payload);
      const channels = Array.isArray(parsed)
        ? parsed
        : ((parsed as { channels?: unknown[] }).channels ?? []);
      if (!Array.isArray(channels) || channels.length === 0) {
        throw new Error("JSON должен быть массивом каналов или объектом с полем channels");
      }
      const result = await importCatalog(channels as Record<string, unknown>[]);
      setMessage(
        `Принято ${result.received}: новых ${result.created}, обновлено ${result.updated}, пропущено ${result.skipped}`,
      );
      setPayload("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Импорт не удался");
    } finally {
      setBusy(false);
    }
  };

  const runMatch = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await matchCatalog();
      setMessage(
        `Сопоставлено ${result.matched}, уже было ${result.already}, без пары ${result.unmatched}`,
      );
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Сопоставление не удалось");
    } finally {
      setBusy(false);
    }
  };

  const submitBlock = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await addBlock(kind, value.trim(), reason.trim() || undefined);
      setValue("");
      setReason("");
      setMessage("Блок добавлен и применён.");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось добавить блок");
    } finally {
      setBusy(false);
    }
  };

  const removeBlock = async (id: number) => {
    setBusy(true);
    setError(null);
    try {
      await deleteBlock(id);
      setMessage("Блок удалён.");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить блок");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {error && <ErrorBanner message={error} />}

      <div className="stat-row">
        <div className="stat">
          <div className="stat-value">{status ? status.catalog_channels : "—"}</div>
          <div className="stat-label">Каналов в каталоге</div>
        </div>
        <div className="stat">
          <div className="stat-value">{status ? status.channels : "—"}</div>
          <div className="stat-label">Каналов в базе</div>
        </div>
        <div className="stat">
          <div className="stat-value">{status ? status.matched_channels : "—"}</div>
          <div className="stat-label">Сопоставлено</div>
        </div>
        <div className="stat">
          <div className="stat-value">{status ? status.unmatched_channels : "—"}</div>
          <div className="stat-label">Без пары</div>
        </div>
        <div className="stat">
          <div className="stat-value">{status ? status.blocks : "—"}</div>
          <div className="stat-label">Блоков</div>
        </div>
      </div>

      {loading && <Loading />}
      {loadError && <ErrorBanner message={loadError} />}
      {message && <p className="tiny">{message}</p>}

      <Card
        title="Импорт каталога"
        actions={
          <button className="btn btn-sm" onClick={() => void runMatch()} disabled={busy}>
            Сопоставить каналы
          </button>
        }
      >
        <form onSubmit={runImport}>
          <div className="field">
            <label htmlFor="catalog-json">JSON каталога (iptv-org: id, name, alt_names, country)</label>
            <textarea
              id="catalog-json"
              className="textarea"
              rows={6}
              placeholder='[{"id": "BBCOne.uk", "name": "BBC One", "country": "GB"}]'
              value={payload}
              onChange={(event) => setPayload(event.target.value)}
            />
          </div>
          <div className="row">
            <input type="file" accept="application/json,.json" onChange={(e) => void loadFile(e)} />
            <div className="spacer" />
            <button className="btn btn-primary" type="submit" disabled={busy || !payload.trim()}>
              Импортировать
            </button>
          </div>
        </form>
        <p className="tiny" style={{ marginBottom: 0 }}>
          Сопоставление идёт по нормализованному названию и псевдонимам с учётом страны. Нечёткого
          совпадения нет намеренно: ошибочно склеить два канала хуже, чем оставить один без пары.
        </p>
      </Card>

      <Card title="Блок-лист" bodyless>
        <div className="card-body">
          <form className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={submitBlock}>
            <div className="field" style={{ flex: "0 0 170px", marginBottom: 0 }}>
              <label htmlFor="block-kind">Тип</label>
              <select
                id="block-kind"
                className="select"
                value={kind}
                onChange={(event) => setKind(event.target.value as BlockKind)}
              >
                {(Object.keys(KIND_LABEL) as BlockKind[]).map((item) => (
                  <option key={item} value={item}>
                    {KIND_LABEL[item]}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: "1 1 220px", marginBottom: 0 }}>
              <label htmlFor="block-value">Значение</label>
              <input
                id="block-value"
                className="input"
                placeholder="BBCOne.uk или provider.example"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                required
              />
            </div>
            <div className="field" style={{ flex: "1 1 200px", marginBottom: 0 }}>
              <label htmlFor="block-reason">Причина</label>
              <input
                id="block-reason"
                className="input"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              Заблокировать
            </button>
          </form>
        </div>

        {blocks.length === 0 ? (
          <div className="empty">Блоков нет.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Тип</th>
                <th>Значение</th>
                <th>Причина</th>
                <th>Добавлен</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {blocks.map((block) => (
                <tr key={block.id}>
                  <td>
                    <StatusPill tone={block.kind === "domain" ? "warn" : "err"}>
                      {KIND_LABEL[block.kind] ?? block.kind}
                    </StatusPill>
                  </td>
                  <td className="mono">{block.value}</td>
                  <td className="muted">{block.reason ?? "—"}</td>
                  <td className="muted">{formatIso(block.created_at)}</td>
                  <td>
                    <button
                      className="btn btn-sm"
                      aria-label={`Удалить блок ${block.value}`}
                      onClick={() => void removeBlock(block.id)}
                      disabled={busy}
                    >
                      Удалить
                    </button>
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
