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
saul-clean            # лёгкая очистка Docker
saul-clean deep       # глубокая очистка вручную
```

`saul-update` больше не запускает установщик автоматически.

## Образы

Этот репозиторий публикует готовый Docker image для быстрого обновления слабых VPS:

```text
ghcr.io/mason4355/saulinfo-site:main
```

Основной `saul-update` использует готовый image и не должен запускать тяжёлую локальную сборку без явного `--source`.
