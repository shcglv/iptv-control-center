import {
  epgStatus,
  healthStreams,
  listChannels,
  listDevices,
  listSources,
  schedulerStatus,
} from "../api";
import { Card, ErrorBanner, Loading, StatusPill, formatEpoch, useAsync } from "../components";

export default function Overview() {
  const { data, error, loading } = useAsync(
    async () => {
      const [scheduler, epg, sources, devices, channels, streams] = await Promise.all([
        schedulerStatus(),
        epgStatus(),
        listSources(),
        listDevices(),
        listChannels("", 1, 0),
        healthStreams(6),
      ]);
      return { scheduler, epg, sources, devices, channels, streams };
    },
    [],
  );

  if (loading) return <Loading />;
  if (error) return <ErrorBanner message={error} />;
  if (!data) return null;

  const healthySources = data.sources.filter((source) => source.last_status === "success").length;
  const brokenSources = data.sources.filter((source) => source.last_status === "error");
  const attention: { tone: "warn" | "err" | "info"; text: string }[] = [];
  if (!data.scheduler.healthy) attention.push({ tone: "warn", text: "Планировщик проверок не отчитывается" });
  for (const source of brokenSources) attention.push({ tone: "err", text: `Источник «${source.name}»: ошибка импорта` });
  if (data.epg.unmapped_channels > 0)
    attention.push({ tone: "info", text: `Без EPG: ${data.epg.unmapped_channels} каналов` });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="stat-row">
        <div className="stat">
          <div className="stat-value">{data.channels.total.toLocaleString("ru-RU")}</div>
          <div className="stat-label">Каналы</div>
        </div>
        <div className="stat">
          <div className="stat-value">{data.sources.length}</div>
          <div className="stat-label">Источники</div>
        </div>
        <div className="stat">
          <div className="stat-value">
            {healthySources}/{data.sources.length}
          </div>
          <div className="stat-label">Импорт без ошибок</div>
        </div>
        <div className="stat">
          <div className="stat-value">{data.devices.length}</div>
          <div className="stat-label">Устройства</div>
        </div>
        <div className="stat">
          <div className="stat-value">{data.scheduler.probed_total.toLocaleString("ru-RU")}</div>
          <div className="stat-label">Проверок потока</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <Card title="Требует внимания">
          {attention.length === 0 ? (
            <div className="muted">Проблем не обнаружено.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {attention.map((item, index) => (
                <div className="row" key={index}>
                  <StatusPill tone={item.tone}>{item.tone === "err" ? "Ошибка" : item.tone === "warn" ? "Внимание" : "Инфо"}</StatusPill>
                  <span>{item.text}</span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Проверки">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="row">
              <StatusPill tone={data.scheduler.healthy ? "ok" : "warn"}>
                {data.scheduler.healthy ? "Работает" : "Нет отчёта"}
              </StatusPill>
              <span className="muted">раундов: {data.scheduler.rounds}</span>
            </div>
            <div className="tiny">последний тик: {formatEpoch(data.scheduler.last_tick_epoch)}</div>
            {data.scheduler.last_error && <div className="tiny">ошибка: {data.scheduler.last_error}</div>}
            <div className="tiny">
              EPG: источников {data.epg.sources}, каналов {data.epg.epg_channels}, программ{" "}
              {data.epg.programmes.toLocaleString("ru-RU")}
            </div>
          </div>
        </Card>
      </div>

      <Card title="Худшие потоки" bodyless>
        {data.streams.length === 0 ? (
          <div className="empty">Проверок ещё не было.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Канал</th>
                <th>Источник</th>
                <th>Uptime</th>
                <th>Score</th>
                <th>Разрешение</th>
                <th>Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {data.streams.map((stream) => (
                <tr key={stream.stream_id}>
                  <td>{stream.name}</td>
                  <td className="muted">{stream.source}</td>
                  <td className="num">{Math.round(stream.uptime_ratio * 100)}%</td>
                  <td className="num">{stream.score.toFixed(1)}</td>
                  <td className="muted">{stream.resolution ?? "—"}</td>
                  <td className="muted">{stream.last_error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
