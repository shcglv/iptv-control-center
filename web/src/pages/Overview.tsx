import { useState } from "react";
import {
  epgStatus,
  healthStreams,
  listChannels,
  listDevices,
  listSources,
  schedulerStatus,
  systemCheck,
  systemInfo,
} from "../api";
import type { SystemCheck } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, formatEpoch, useAsync } from "../components";

export default function Overview() {
  const [checks, setChecks] = useState<SystemCheck[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const { data, error, loading } = useAsync(
    async () => {
      const [scheduler, epg, sources, devices, channels, streams, system] = await Promise.all([
        schedulerStatus(),
        epgStatus(),
        listSources(),
        listDevices(),
        listChannels("", 1, 0),
        healthStreams(6),
        systemInfo(),
      ]);
      return { scheduler, epg, sources, devices, channels, streams, system };
    },
    [],
  );

  const runChecks = async () => {
    setChecking(true);
    setCheckError(null);
    try {
      setChecks(await systemCheck());
    } catch (err) {
      setCheckError(err instanceof Error ? err.message : "Проверка не удалась");
    } finally {
      setChecking(false);
    }
  };

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

      <div className="split-grid">
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

      <Card
        title="Система"
        actions={
          <button className="btn btn-sm" onClick={() => void runChecks()} disabled={checking}>
            {checking ? "Проверяем…" : "Проверить"}
          </button>
        }
      >
        <div className="row" style={{ flexWrap: "wrap", gap: 24, marginBottom: 12 }}>
          <div>
            <div className="tiny">Версия</div>
            <div>{data.system.version}</div>
          </div>
          <div>
            <div className="tiny">База данных</div>
            <div>{data.system.database}</div>
          </div>
          <div>
            <div className="tiny">Миграции</div>
            <div className="muted">{data.system.migrations ?? "не применены"}</div>
          </div>
          <div>
            <div className="tiny">Окружение</div>
            <div className="muted">{data.system.app_env}</div>
          </div>
        </div>
        {checkError && <ErrorBanner message={checkError} />}
        {checks && (
          <table>
            <thead>
              <tr>
                <th>Проверка</th>
                <th style={{ width: 90 }}>Статус</th>
                <th>Детали</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => (
                <tr key={check.name}>
                  <td>{check.name}</td>
                  <td>
                    <StatusPill
                      tone={check.status === "ok" ? "ok" : check.status === "fail" ? "err" : "warn"}
                    >
                      {check.status}
                    </StatusPill>
                  </td>
                  <td className="muted">{check.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="tiny" style={{ marginBottom: 0 }}>
          Проверка выполняется на сервере и не требует доступа к его консоли — то же, что
          запускает <code>scripts/preflight.py</code>.
        </p>
      </Card>
    </div>
  );
}
