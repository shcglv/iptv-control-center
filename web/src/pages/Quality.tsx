import { useState } from "react";
import { healthStreams, runSchedulerTick } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, useAsync } from "../components";

export default function Quality() {
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { data, error, loading } = useAsync(() => healthStreams(50), [reload]);

  const runCheck = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const stats = await runSchedulerTick();
      setMessage(
        stats.skipped
          ? "Раунд уже идёт (планировщик держит блокировку)."
          : `Проверено: ${stats.probed}, успешно: ${stats.ok}, ошибок: ${stats.failed}`,
      );
      setReload((value) => value + 1);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Не удалось запустить проверку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        title="Проверки потоков"
        actions={
          <button className="btn btn-sm btn-primary" onClick={runCheck} disabled={busy}>
            {busy ? "Проверка…" : "Проверить сейчас"}
          </button>
        }
        bodyless
      >
        {message && <div className="card-body"><div className="muted">{message}</div></div>}
        {loading && <Loading />}
        {error && <div className="card-body"><ErrorBanner message={error} /></div>}
        {data && data.length === 0 && <div className="empty">Данных ещё нет — запустите проверку.</div>}
        {data && data.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Поток</th>
                <th>Источник</th>
                <th>Контейнер</th>
                <th style={{ width: 150 }}>Uptime</th>
                <th style={{ width: 90 }}>Score</th>
                <th>Разрешение</th>
                <th>Последняя ошибка</th>
              </tr>
            </thead>
            <tbody>
              {data.map((stream) => {
                const tone = stream.uptime_ratio >= 0.9 ? "ok" : stream.uptime_ratio >= 0.5 ? "warn" : "err";
                return (
                  <tr key={stream.stream_id}>
                    <td>{stream.name}</td>
                    <td className="muted">{stream.source}</td>
                    <td className="mono muted">{stream.container}</td>
                    <td>
                      <StatusPill tone={tone}>{Math.round(stream.uptime_ratio * 100)}%</StatusPill>
                    </td>
                    <td className="num">{stream.score.toFixed(1)}</td>
                    <td className="muted">{stream.resolution ?? "—"}</td>
                    <td className="muted">{stream.last_error ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      <p className="tiny">
        Серверная проверка не равна качеству воспроизведения на устройстве: другой маршрут, другой провайдер, другие ограничения.
      </p>
    </div>
  );
}
