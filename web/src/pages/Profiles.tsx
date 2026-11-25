import { Fragment, useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  ApiError,
  clearProfileChannel,
  createProfile,
  listChannelSources,
  listProfileChannels,
  listProfiles,
  setProfileChannel,
  setProfileOrder,
} from "../api";
import type { ChannelSource, ProfileChannel } from "../api";
import { Card, ErrorBanner, Loading, StatusPill, useAsync } from "../components";

const BIG = 1_000_000;

export default function Profiles() {
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const profiles = useAsync(listProfiles, [reload]);
  const [rows, setRows] = useState<ProfileChannel[] | null>(null);
  const [pinPanel, setPinPanel] = useState<string | null>(null);
  const [sources, setSources] = useState<Record<string, ChannelSource[]>>({});

  const current = profiles.data?.find((profile) => profile.id === selected) ?? null;

  useEffect(() => {
    if (selected === null) {
      setRows(null);
      return;
    }
    let alive = true;
    listProfileChannels(selected)
      .then((data) => {
        if (alive) setRows(data);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [selected, reload]);

  const refresh = () => setReload((value) => value + 1);

  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);

  const orderOf = (row: ProfileChannel) => row.sort_index ?? row.number ?? BIG;

  /** Drag-and-drop: one write when an integer slot exists, one batch request when it does not. */
  const reorder = async (sourceKey: string, targetKey: string) => {
    if (!rows || selected === null || sourceKey === targetKey) return;
    const list = [...rows];
    const from = list.findIndex((item) => item.public_key === sourceKey);
    const to = list.findIndex((item) => item.public_key === targetKey);
    if (from < 0 || to < 0) return;

    const [moved] = list.splice(from, 1);
    list.splice(to, 0, moved);
    const previous = list[to - 1];
    const next = list[to + 1];

    if (!previous && !next) return;
    if (!previous) {
      await patch(moved, { sort_index: orderOf(next) - 1 });
      return;
    }
    if (!next) {
      await patch(moved, { sort_index: orderOf(previous) + 1 });
      return;
    }

    const low = orderOf(previous);
    const high = orderOf(next);
    if (high - low >= 2) {
      await patch(moved, { sort_index: Math.floor((low + high) / 2) });
      return;
    }

    // Two neighbouring rows already sit on consecutive integers: persist the whole visible order.
    setBusy(true);
    setError(null);
    try {
      await setProfileOrder(
        selected,
        list.map((item) => item.public_key),
      );
      setRows(list.map((item, index) => ({ ...item, sort_index: index + 1, overridden: true })));
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить порядок");
    } finally {
      setBusy(false);
    }
  };

  const addProfile = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await createProfile(name.trim());
      setName("");
      refresh();
      setSelected(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать профиль");
    } finally {
      setBusy(false);
    }
  };

  const patch = async (
    row: ProfileChannel,
    changes: { visible?: boolean; sort_index?: number | null; pinned_stream_id?: number | null },
  ) => {
    if (selected === null) return;
    const nextVisible = changes.visible ?? row.visible;
    const nextSort = changes.sort_index !== undefined ? changes.sort_index : row.sort_index;
    const nextPinned =
      changes.pinned_stream_id !== undefined ? changes.pinned_stream_id : row.pinned_stream_id;

    // Optimistic: the checkbox must react to the click, not to the round trip.
    setRows((current) =>
      current?.map((item) =>
        item.public_key === row.public_key
          ? {
              ...item,
              visible: nextVisible,
              sort_index: nextSort,
              pinned_stream_id: nextPinned,
              overridden: true,
            }
          : item,
      ) ?? current,
    );

    setBusy(true);
    setError(null);
    try {
      await setProfileChannel(selected, row.public_key, {
        visible: nextVisible,
        sort_index: nextSort,
        pinned_stream_id: nextPinned,
      });
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить изменение");
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const reset = async (row: ProfileChannel) => {
    if (selected === null) return;
    setBusy(true);
    setError(null);
    try {
      await clearProfileChannel(selected, row.public_key);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сбросить переопределение");
    } finally {
      setBusy(false);
    }
  };

  // The arrows are the keyboard-accessible path and share the drag logic: both insert the row at
  // the neighbour's position, so they cannot collide with the row after it.
  const move = (row: ProfileChannel, direction: -1 | 1) => {
    if (!rows) return;
    const index = rows.findIndex((item) => item.public_key === row.public_key);
    const neighbour = rows[index + direction];
    if (!neighbour) return;
    void reorder(row.public_key, neighbour.public_key);
  };

  const openPin = async (row: ProfileChannel) => {
    if (pinPanel === row.public_key) {
      setPinPanel(null);
      return;
    }
    setPinPanel(row.public_key);
    if (sources[row.public_key]) return;
    try {
      const data = await listChannelSources(row.public_key);
      setSources((current) => ({ ...current, [row.public_key]: data }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить источники");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {error && <ErrorBanner message={error} />}

      <Card title="Профили плейлистов" bodyless>
        {profiles.loading && <Loading />}
        {profiles.error && (
          <div className="card-body">
            <ErrorBanner message={profiles.error} />
          </div>
        )}
        {profiles.data && profiles.data.length === 0 && (
          <div className="empty">Профилей нет — создайте первый ниже.</div>
        )}
        {profiles.data && profiles.data.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Название</th>
                <th>Стратегия</th>
                <th>Устройства</th>
                <th>Каналы</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.data.map((profile) => (
                <tr key={profile.id}>
                  <td>{profile.name}</td>
                  <td className="muted">{profile.strategy}</td>
                  <td className="num">{profile.devices}</td>
                  <td className="num">
                    {profile.channels_visible} / {profile.channels_total}
                  </td>
                  <td>
                    <button
                      className="btn btn-sm"
                      onClick={() => {
                        setSelected(profile.id);
                        setPinPanel(null);
                      }}
                    >
                      Редактировать
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="card-body">
          <form className="row" onSubmit={addProfile}>
            <input
              className="input"
              placeholder="Название профиля"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              style={{ maxWidth: 280 }}
            />
            <button className="btn btn-primary" type="submit" disabled={busy}>
              Создать
            </button>
          </form>
        </div>
      </Card>

      {current && (
        <Card
          title={`Состав профиля · ${current.name}`}
          actions={
            <span className="tiny">
              видно {current.channels_visible} из {current.channels_total}
            </span>
          }
          bodyless
        >
          {!rows && <Loading />}
          {rows && (
            <table
              onPointerUp={() => {
                if (dragKey && overKey && dragKey !== overKey) {
                  void reorder(dragKey, overKey);
                }
                setDragKey(null);
                setOverKey(null);
              }}
              onPointerLeave={() => {
                if (dragKey) setOverKey(dragKey);
              }}
            >
              <thead>
                <tr>
                  <th style={{ width: 28 }} aria-label="Порядок" />
                  <th style={{ width: 70 }}>№</th>
                  <th>Канал</th>
                  <th>Группа</th>
                  <th style={{ width: 90 }}>Виден</th>
                  <th style={{ width: 110 }}>Порядок</th>
                  <th style={{ width: 190 }}>Источник</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Fragment key={row.public_key}>
                    <tr
                      data-key={row.public_key}
                      data-name={row.name}
                      onPointerEnter={() => {
                        if (dragKey && dragKey !== row.public_key) setOverKey(row.public_key);
                      }}
                      style={
                        overKey === row.public_key && dragKey
                          ? { outline: "2px dashed var(--accent)", outlineOffset: -2 }
                          : dragKey === row.public_key
                            ? { opacity: 0.5 }
                            : undefined
                      }
                    >
                      <td
                        className="muted"
                        style={{ cursor: "grab", touchAction: "none", userSelect: "none" }}
                        title="Перетащите для изменения порядка"
                        data-drag-handle={row.public_key}
                        onPointerDown={(event) => {
                          event.preventDefault();
                          setDragKey(row.public_key);
                          setOverKey(row.public_key);
                        }}
                      >
                        ⠿
                      </td>
                      <td className="num">{row.number ?? "—"}</td>
                      <td>
                        {row.name} {row.overridden && <span className="tiny">· изменён</span>}
                      </td>
                      <td className="muted">{row.group ?? "—"}</td>
                      <td>
                        <input
                          type="checkbox"
                          checked={row.visible}
                          disabled={busy}
                          aria-label={`Видимость ${row.name}`}
                          onChange={() => void patch(row, { visible: !row.visible })}
                        />
                      </td>
                      <td>
                        <button className="btn btn-sm" onClick={() => move(row, -1)} disabled={busy}>
                          ↑
                        </button>{" "}
                        <button className="btn btn-sm" onClick={() => move(row, 1)} disabled={busy}>
                          ↓
                        </button>
                      </td>
                      <td>
                        <button className="btn btn-sm" onClick={() => void openPin(row)}>
                          {row.pinned_stream_id ? (
                            <StatusPill tone="info">закреплён</StatusPill>
                          ) : (
                            "закрепить"
                          )}
                        </button>{" "}
                        {row.overridden && (
                          <button className="btn btn-sm" onClick={() => void reset(row)} disabled={busy}>
                            сброс
                          </button>
                        )}
                      </td>
                    </tr>
                    {pinPanel === row.public_key && (
                      <tr key={`${row.public_key}-pin`}>
                        <td />
                        <td colSpan={6}>
                          {!sources[row.public_key] && <span className="tiny">Загрузка источников…</span>}
                          {sources[row.public_key]?.length === 0 && (
                            <span className="tiny">У канала нет доступных источников.</span>
                          )}
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {(sources[row.public_key] ?? []).map((source) => (
                              <div className="row" key={source.stream_id}>
                                <StatusPill tone={source.uptime_ratio >= 0.9 ? "ok" : "warn"}>
                                  {Math.round(source.uptime_ratio * 100)}%
                                </StatusPill>
                                <span>
                                  {source.source} · {source.container} · {source.resolution ?? "—"} · score{" "}
                                  {source.score.toFixed(1)}
                                </span>
                                <div className="spacer" />
                                <button
                                  className="btn btn-sm"
                                  onClick={() => {
                                    void patch(row, { pinned_stream_id: source.stream_id });
                                    setPinPanel(null);
                                  }}
                                >
                                  Закрепить
                                </button>
                              </div>
                            ))}
                            {row.pinned_stream_id && (
                              <div className="row">
                                <button
                                  className="btn btn-sm"
                                  onClick={() => {
                                    void patch(row, { pinned_stream_id: null });
                                    setPinPanel(null);
                                  }}
                                >
                                  Снять закрепление
                                </button>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
      <p className="tiny">
        Профиль хранит только отличия от каталога: скрытые каналы, порядок и закреплённый источник.
        Изменения доедут до устройств при следующем запросе плейлиста.
      </p>
    </div>
  );
}
