# Как один раз залить бота в GitHub (чтобы Bothost подхватил)

Репозиторий у тебя уже есть: **https://github.com/nastyavolochugina99-oss/rostum-ege-bot**  
Он пустой — нужно отправить туда код из папки бота.

## Шаг 1. Открой терминал

В Cursor: **View → Terminal** или **Ctrl+`**.

## Шаг 2. Выполни команды по порядку

Копируй и вставляй блоки в терминал (после каждой команды нажимай Enter).

```bash
cd "/Users/anastasia.volochugina/workspace/projects/active/Ростум | ЕГЭ/tutor-booking-bot"
```

```bash
git init
```

```bash
git add .
```

*(В репозиторий попадут все файлы, кроме тех, что в .gitignore: не попадёт .env и data/bookings.db — так и нужно.)*

```bash
git status
```

*(Проверь: в списке должны быть bot.py, storage.py, requirements.txt, data/slots.json. Не должно быть .env и data/bookings.db.)*

```bash
git commit -m "Ростум ЕГЭ: бот записи на тьюторские сессии"
```

```bash
git branch -M main
```

```bash
git remote add origin https://github.com/nastyavolochugina99-oss/rostum-ege-bot.git
```

*(Если напишет "remote origin already exists" — выполни: `git remote set-url origin https://github.com/nastyavolochugina99-oss/rostum-ege-bot.git`)*

```bash
git push -u origin main
```

Тебя попросят логин и пароль. На GitHub сейчас пароли не принимают — нужен **Personal Access Token**:

1. Зайди на https://github.com/settings/tokens  
2. **Generate new token (classic)**  
3. Название любое (например "Bothost"), галочку **repo** поставь  
4. Сгенерируй и скопируй токен  
5. В терминале при запросе пароля вставь этот токен (логин — твой GitHub-логин)

## Шаг 3. После успешного push

Зайди на https://github.com/nastyavolochugina99-oss/rostum-ege-bot — там должны появиться файлы и ветка **main**.

В Bothost нажми **Пересобрать** / **Rebuild**. Сборка должна пройти: Bothost клонирует репозиторий и найдёт ветку `main`.
