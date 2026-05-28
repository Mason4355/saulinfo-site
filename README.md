# SaulInfo Site

Служебный репозиторий кабинета/сайта SaulInfo. Основная установка, обновление и команды обслуживания находятся в основном репозитории:

```text
https://github.com/Mason4355/shop-update
```

## Главные команды

Установка только для нового сервера или явной переустановки:

```bash
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-install.sh')
```

Обновление только для уже существующего проекта:

```bash
SAULINFO_SKIP_DOCTOR=1 bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-update.sh') --rebuild
```

После установки/обновления:

```bash
saul-install          # явная установка/переустановка
saul-update --rebuild # обновление существующего проекта
saul-doctor --fix     # диагностика и ремонт
saul-repair-nginx     # восстановить nginx-routing
saul-reset-panel-admin # восстановить вход admin/admin; ADMIN_ID необязателен
saul-clean            # лёгкая очистка Docker
saul-clean deep       # глубокая очистка вручную
saul-uninstall --force # полностью удалить проект
```

Установка через `bootstrap-install.sh` или `saul-install` при каждом запуске снова спрашивает параметры, используя старые ответы только как значения по умолчанию. При первой установке логин и пароль панели по умолчанию: `admin` / `admin`.

Nginx в установщике можно отключить: на вопрос `Create/update nginx config and certbot automatically` ответьте `n`. Тогда основной установщик не будет создавать или править nginx-конфиг, не запустит certbot и выведет готовые `location` для ручного reverse proxy в конце установки.

Для одного домена укажите одинаковый домен кабинета и панели: главная страница откроет кабинет, а админка будет на `/panel/`. Выбранные внутренние порты доступны только через `127.0.0.1`.

Полное удаление проекта:

```bash
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-uninstall.sh') --force
```

`saul-update` больше не запускает установщик автоматически.

## Оба Telegram-бота на зарубежном VPS

Кабинет, база и Remnawave остаются на основном сервере. Основной и support-бот
запускаются отдельным worker на зарубежном сервере.

На основном сервере:

```bash
cd /root/shop-update
bash deploy/enable-core-worker-api.sh 'https://www.example.com'
```

На зарубежном сервере подставьте выданные URL API и токен:

```bash
SHOPBOT_CORE_API_URL='https://www.example.com/panel/internal/worker' \
SHOPBOT_CORE_API_TOKEN='ТОКЕН_С_ОСНОВНОГО_СЕРВЕРА' \
bash <(curl -H 'Cache-Control: no-cache' -fsSL 'https://raw.githubusercontent.com/Mason4355/shop-update/main/deploy/bootstrap-bot-worker.sh')
```

На зарубежный сервер основной проект не копируется. Установщик оставляет
только:

```text
/opt/saulinfo-telegram-worker/docker-compose.yml
/opt/saulinfo-telegram-worker/.env
```

Обновления после разделения:

```bash
# основной сервер
saul-update --rebuild

# зарубежный сервер
saul-bot-update
saul-bot-logs
```

Если на зарубежном сервере осталась старая worker-копия `/root/shop-update`,
повторная установка перенесёт настройки и удалит её, только если там нет
основного `.env`.

## Образы

Этот репозиторий публикует готовый Docker image для быстрого обновления слабых VPS:

```text
ghcr.io/mason4355/saulinfo-site:main
```

Основной `saul-update` использует готовый image и не должен запускать тяжёлую локальную сборку без явного `--source`.
