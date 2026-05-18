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

