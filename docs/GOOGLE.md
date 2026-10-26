# Подключение Google (почта, календарь, задачи)

Капри **читает** почту (только непрочитанные во «Входящих»: от кого и тема), **читает и добавляет** события в календарь
и **читает и добавляет** задачи в Google Tasks. Письма она не отправляет и ничего не удаляет.

Это делается один раз, минут 10. Заодно — живая практика с Google Cloud: проект, API, OAuth.

## 1. Проект и API

1. Открой <https://console.cloud.google.com/> под своим Google-аккаунтом.
2. Вверху: **Select a project → New project**, имя например `capri`, **Create**.
3. **APIs & Services → Library**, найди и нажми **Enable** у трёх API:
   - Gmail API
   - Google Calendar API
   - Google Tasks API

## 2. Экран согласия (OAuth consent screen)

1. **APIs & Services → OAuth consent screen** (в новом интерфейсе — **Google Auth Platform → Branding**).
2. Тип пользователей: **External**. Имя приложения: `Capri`, почта поддержки и контакт — твоя почта. Сохрани.
3. **Audience / Test users → Add users** — добавь свою почту.
4. Чтобы вход не слетал каждые 7 дней: **Audience → Publish app** (статус *In production*).
   Проверка Google не нужна — при входе просто будет экран «Google hasn't verified this app»:
   жми **Advanced → Go to Capri (unsafe)**. Это твоё собственное приложение, так и должно быть.

## 3. Ключ для Капри

1. **APIs & Services → Credentials → Create credentials → OAuth client ID** (или **Clients → Create client**).
2. Application type: **Desktop app**, имя `Capri desktop` → **Create**.
3. **Download JSON** и сохрани файл как
   ```
   E:\Project\Capri\data\google_client.json
   ```
   (папка `data` в git не попадает — файл останется только у тебя).

## 4. Подключить

Перезапусти Капри (`run_capri.bat` сам поставит пакеты Google) и скажи: **«Капри, подключи гугл»**.
Откроется браузер → выбери аккаунт → разреши доступ. Готово: токен лежит в `data\google_token.json`.

Отключить: удали `data\google_token.json` и/или отзови доступ на <https://myaccount.google.com/permissions>.

## Что говорить

| Фраза | Что будет |
|---|---|
| есть новые письма? · проверь почту | сколько непрочитанных и от кого (3 последних) |
| что у меня сегодня · что у меня завтра в календаре · какие планы на пятницу | события на день |
| добавь в календарь завтра в 15:00 собеседование в XCOM | спросит «Добавить?» и создаст событие на час |
| добавь в календарь послезавтра день рождения мамы | событие на весь день |
| добавь задачу купить хлеб · запиши в список дел позвонить маме | задача в Google Tasks |
| какие у меня задачи | невыполненные задачи |
