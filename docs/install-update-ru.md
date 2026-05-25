# Установка и обновление SaulInfo

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
saul-clean
saul-clean deep
```

`saul-update` не запускает установщик автоматически.

## Оба Telegram-бота на зарубежном VPS

Кабинет, база, платежи и Remnawave остаются на основном сервере. На зарубежном
сервере запускается отдельный worker для основного и support-бота.

На основном сервере:

```bash
cd /root/shop-update
bash deploy/enable-core-worker-api.sh 'https://panel.example.com'
```

На зарубежном сервере:

```bash
SHOPBOT_CORE_API_URL='https://panel.example.com/control-room-saul/internal/worker' \
SHOPBOT_CORE_API_TOKEN='ТОКЕН_С_ОСНОВНОГО_СЕРВЕРА' \
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-bot-worker.sh')
```

Для обновления используйте `saul-update --rebuild` на основном сервере и
`saul-bot-update` на зарубежном сервере.
При переходе со старой схемы worker удаляет прежний контейнер шлюза как
неиспользуемый.
