import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, createDevice, deleteDevice, listDevices, listProfiles, rotateDevice, updateDevice } from "../api";
import type { DeviceWithToken } from "../api";
import { Card, ErrorBanner, Loading, formatIso, useAsync } from "../components";

export default function Devices() {
  const [reload, setReload] = useState(0);
  const { data, error, loading } = useAsync(
    async () => {
      const [devices, profiles] = await Promise.all([listDevices(), listProfiles()]);
      return { devices, profiles };
    },
    [reload],
  );

  const [name, setName] = useState("");
  const [kind, setKind] = useState("android_tv");
  const [profileId, setProfileId] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<DeviceWithToken | null>(null);

  const addDevice = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    try {
      const created = await createDevice(name.trim(), kind, profileId === "" ? undefined : Number(profileId));
      setName("");
      setRevealed(created);
      setReload((value) => value + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Не удалось создать устройство");
    } finally {
      setBusy(false);
    }
  };

  const rotate = async (id: string) => {
    setBusy(true);
    setActionError(null);
    try {
      const rotated = await rotateDevice(id);
      setRevealed(rotated);
      setReload((value) => value + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Не удалось выполнить ротацию");
    } finally {
      setBusy(false);
    }
  };

  const rename = async (id: string, current: string) => {
    const next = window.prompt("Новое название устройства", current)?.trim();
    if (!next || next === current) return;
    setBusy(true);
    setActionError(null);
    try {
      await updateDevice(id, { name: next });
      setReload((value) => value + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Не удалось переименовать");
    } finally {
      setBusy(false);
    }
  };

  const moveToProfile = async (id: string, profile: number) => {
    setBusy(true);
    setActionError(null);
    try {
      await updateDevice(id, { profile_id: profile });
      setReload((value) => value + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Не удалось сменить профиль");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string, label: string) => {
    if (!window.confirm(`Удалить устройство «${label}»? Его ссылка перестанет работать.`)) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteDevice(id);
      setReload((value) => value + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Не удалось удалить устройство");
    } finally {
      setBusy(false);
    }
  };

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      /* clipboard may be unavailable outside a secure context */
    }
  };

  const playlistUrl = revealed
    ? `${window.location.origin}/p/${revealed.token}/live.m3u`
    : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {revealed && (
        <Card title="Токен устройства — показывается один раз">
          <p className="muted" style={{ marginTop: 0 }}>
            Устройство «{revealed.name}». Сохраните ссылку: повторно токен не отображается, только ротация.
          </p>
          <div className="code">{playlistUrl}</div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn btn-sm" onClick={() => copy(playlistUrl)}>
              Копировать M3U
            </button>
            <button
              className="btn btn-sm"
              onClick={() => copy(`${window.location.origin}/p/${revealed.token}/epg.xml`)}
            >
              Копировать EPG
            </button>
            <div className="spacer" />
            <button className="btn btn-sm" onClick={() => setRevealed(null)}>
              Скрыть
            </button>
          </div>
        </Card>
      )}

      {actionError && <ErrorBanner message={actionError} />}

      <Card title="Устройства" bodyless>
        {loading && <Loading />}
        {error && <div className="card-body"><ErrorBanner message={error} /></div>}
        {data && data.devices.length === 0 && <div className="empty">Устройств нет — добавьте первое ниже.</div>}
        {data && data.devices.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Название</th>
                <th>Тип</th>
                <th>Токен</th>
                <th>Создано</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.devices.map((device) => (
                <tr key={device.id}>
                  <td>{device.name}</td>
                  <td className="muted">{device.kind}</td>
                  <td className="mono muted">{device.token_prefix}…</td>
                  <td className="muted">{formatIso(device.created_at)}</td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <button className="btn btn-sm" onClick={() => rotate(device.id)} disabled={busy}>
                        Ротация
                      </button>
                      <button
                        className="btn btn-sm"
                        onClick={() => rename(device.id, device.name)}
                        disabled={busy}
                      >
                        Переименовать
                      </button>
                      <select
                        className="select"
                        style={{ width: 150 }}
                        value={device.profile_id ?? ""}
                        onChange={(event) => moveToProfile(device.id, Number(event.target.value))}
                        disabled={busy}
                        title="Профиль"
                      >
                        {(data?.profiles ?? []).map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.name}
                          </option>
                        ))}
                      </select>
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => remove(device.id, device.name)}
                        disabled={busy}
                      >
                        Удалить
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Новое устройство">
        <form onSubmit={addDevice}>
          <div className="row" style={{ alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
            <div className="field" style={{ flex: "1 1 200px", marginBottom: 0 }}>
              <label htmlFor="device-name">Название</label>
              <input
                id="device-name"
                className="input"
                placeholder="Living Room"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            <div className="field" style={{ flex: "0 0 160px", marginBottom: 0 }}>
              <label htmlFor="device-kind">Тип</label>
              <select id="device-kind" className="select" value={kind} onChange={(event) => setKind(event.target.value)}>
                <option value="android_tv">Android TV</option>
                <option value="apple_tv">Apple TV</option>
                <option value="desktop">Desktop</option>
                <option value="mobile">Mobile</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div className="field" style={{ flex: "0 0 200px", marginBottom: 0 }}>
              <label htmlFor="device-profile">Профиль</label>
              <select
                id="device-profile"
                className="select"
                value={profileId}
                onChange={(event) => setProfileId(event.target.value === "" ? "" : Number(event.target.value))}
              >
                <option value="">по умолчанию</option>
                {(data?.profiles ?? []).map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              Добавить
            </button>
          </div>
        </form>
        <p className="tiny" style={{ marginBottom: 0 }}>
          Плеер получает M3U и XMLTV по ссылке с токеном устройства. Отзыв — через ротацию.
        </p>
      </Card>
    </div>
  );
}
