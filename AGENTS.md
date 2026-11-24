# AGENTS.md — правила для агентов в этом репозитории

## 1. Что это за репозиторий

`iptv-control-center` — self-hosted контрольная плоскость IPTV. Сервер управляет источниками,
каталогом, EPG, качеством и устройствами, но **не передаёт видео**. Лицензия: `AGPL-3.0-only`.

## 2. Жёсткие инварианты (нарушение = стоп)

1. **Никакого видеотранзита.** Ни прокси, ни транскодинга, ни ретрансляции в штатной архитектуре.
   Ответ `302` — не прокси: он не содержит медиабайт.
2. **Секреты не попадают в Git, логи, тесты и сообщения.** Провайдерские URL шифруются
   (AES-256-GCM); в логах — только хост и маскированный путь.
3. **Никаких секретов в `callback_data`**, URL-параметрах аналитики и сообщениях об ошибках.
4. **Миграции только вперёд.** Схема меняется исключительно через Alembic.
5. **Детерминизм вывода.** Один и тот же каталог + профиль дают байт-идентичный M3U/XMLTV.
6. **Никаких правок production-инфраструктуры из этого репозитория.** Деплой — только через
   Coolify и только по согласованному плану.

## 3. Обязательно

- `ruff check` + `mypy src` + `pytest` зелёные перед коммитом.
- Тесты на каждый новый парсер/рендерер: golden-файлы для M3U/XMLTV.
- Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`.
- Любое отклонение от `docs/ARCHITECTURE.md` — сначала ADR в `docs/decisions/`.

## 4. Проверки

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest -q
```

## 5. Секреты и окружение

Локально — `.env` (gitignored) или переменные `IPTV_*`. В production — только env Coolify
(locked / shown-once). `IPTV_ADMIN_API_KEY` — временный гейт до появления входа с TOTP.

## 6. Карта репозитория

`src/iptvcc/{config,db,models,secrets,m3u,matching,api}.py`, `src/iptvcc/services/`,
`tests/`, `docs/`.

## 7. Если контекст утерян

Читать в порядке: `docs/ARCHITECTURE.md` → `docs/ROADMAP.md` → `docs/decisions/` → текущие тесты.
