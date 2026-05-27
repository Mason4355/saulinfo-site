# Установка и обновление SaulInfo

`bootstrap-install.sh` и `saul-install` при каждом запуске заново спрашивают параметры установки. Старые ответы используются только как значения по умолчанию. При чистой установке вход по умолчанию: `admin` / `admin`.

Рекомендуется указать один домен и для кабинета, и для панели: `/` открывает кабинет, `/panel/` открывает админку. Порты сервисов выбираются при установке и открываются только локально на сервере.

## Полное удаление

```bash
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-uninstall.sh') --force
```

После установки также доступно:

```bash
saul-uninstall --force
```

Этот репозиторий служебный. Установка и обновление выполняются через основной репозиторий `Mason4355/shop-update`.

## Установка

Только новый сервер или явная переустановка:

```bash
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-install.sh')
```

## Обновление

Только существующий проект:

```bash
SAULINFO_SKIP_DOCTOR=1 bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-update.sh') --rebuild
```

После первого успешного обновления:

```bash
saul-update --rebuild
```

## Команды

```bash
saul-install
saul-update --rebuild
saul-doctor --fix
saul-repair-nginx
saul-reset-panel-admin
saul-clean
saul-clean deep
saul-uninstall --force
```

Для одновременной привязки владельца панели к Telegram: `ADMIN_ID=123456789 saul-reset-panel-admin`.

`saul-update` не запускает установщик автоматически.

## Оба Telegram-бота на зарубежном VPS

Кабинет, база, платежи и Remnawave остаются на основном сервере. На зарубежном
сервере запускается отдельный worker для основного и support-бота.

На основном сервере:

```bash
cd /root/shop-update
bash deploy/enable-core-worker-api.sh 'https://www.example.com'
```

На зарубежном сервере:

```bash
SHOPBOT_CORE_API_URL='https://www.example.com/panel/internal/worker' \
SHOPBOT_CORE_API_TOKEN='ТОКЕН_С_ОСНОВНОГО_СЕРВЕРА' \
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-bot-worker.sh')
```

Зарубежный VPS содержит только рабочие файлы контейнера:

```text
/opt/saulinfo-telegram-worker/docker-compose.yml
/opt/saulinfo-telegram-worker/.env
```

Для обновления используйте `saul-update --rebuild` на основном сервере и
`saul-bot-update` на зарубежном сервере.
Старая worker-копия `/root/shop-update` удаляется повторной установкой, только
если это не основной сервер с `.env`.
