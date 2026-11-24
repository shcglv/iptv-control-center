import { useState } from "react";
import { listChannels } from "../api";
import { Card, ErrorBanner, Loading, useAsync } from "../components";

const PAGE_SIZE = 25;

export default function Channels() {
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);

  const { data, error, loading } = useAsync(() => listChannels(search, PAGE_SIZE, offset), [search, offset]);

  const applySearch = (event: React.FormEvent) => {
    event.preventDefault();
    setOffset(0);
    setSearch(draft.trim());
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
    </div>
  );
}
