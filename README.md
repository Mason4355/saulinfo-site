# SaulInfo Cabinet Files

Этот репозиторий больше не является отдельным пользовательским сайтом.

Основной проект SaulInfo находится здесь:

```text
git@github.com:Mason4355/shop-update.git
```

В основном репозитории находятся:

- административная панель;
- Telegram-бот;
- support-бот;
- пользовательский кабинет;
- установщик;
- команды обновления;
- документация по установке и переносу.

Этот репозиторий оставлен как служебный:

- для совместимости с текущим установщиком;
- для обновлений;
- для файлов, которые могут понадобиться кабинету;
- для плавного перехода после отказа от старого отдельного сайта.

Установка и обновление выполняются из `shop-update`.

На сервере используйте:

```bash
saul-update
```

Документация:

```text
/root/shop-update/README.md
/root/shop-update/docs/FINAL_DEPLOY.md
```

## Fast image updates

This repository publishes a ready-to-run Docker image for faster VPS updates:

```text
ghcr.io/mason4355/saulinfo-site:main
```

`saul-update` pulls this image first and falls back to local build only if the image is not ready yet.
