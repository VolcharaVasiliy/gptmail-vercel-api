/**
 * GPTMail — получить токен верификации браузера и скопировать его в буфер.
 *
 * Как пользоваться:
 *   1. Откройте https://mail.chatgpt.org.uk (любая страница).
 *   2. Нажмите F12, откройте вкладку Console.
 *   3. Вставьте этот скрипт целиком и нажмите Enter.
 *   4. Вверху справа на секунду появится виджет проверки — дождитесь строки
 *      "✔ Токен ...". Токен уже в буфере обмена.
 *
 * Токен действует ~5 минут и одноразовый, поэтому сразу обменяйте его на
 * сессию: POST { "turnstile_token": "<токен>" } на /api/verify-browser
 * вашего API. Ответ содержит state, который остаётся валидным весь день
 * (~24 часа) и сам продлевает внутренние токены GPTMail.
 *
 * Ленивый вариант: впишите адрес своего API в API_BASE — тогда скрипт сам
 * обменяет токен и скопирует в буфер готовый к использованию state:
 *
 *   const API_BASE = "";            // например: "https://your-app.vercel.app"
 *   const API_TOKEN = "";           // ваш API_BEARER_TOKEN, если включён
 */

(() => {
  "use strict";

  const API_BASE = "";   // <-- можно вписать адрес своего задеплоенного API
  const API_TOKEN = "";  // <-- Bearer-токен API, если включена защита

  if (location.hostname !== "mail.chatgpt.org.uk") {
    console.error("✖ Откройте этот скрипт на странице mail.chatgpt.org.uk");
    return;
  }

  const SITEKEY = "0x4AAAAAAD9zdhyrcm6dCJRt";

  const copyToClipboard = async (text) => {
    if (typeof copy === "function") {
      // Встроенная функция консоли DevTools
      copy(text);
      return "скопировано в буфер обмена";
    }
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return "скопировано в буфер обмена";
    }
    return "скопировать не удалось — выделите текст ниже и скопируйте вручную";
  };

  const log = (mark, msg) => console.log(`%c${mark} ${msg}`, "font-weight:bold");

  (async () => {
    try {
      // 1. Загружаем Turnstile, если страница его ещё не загрузила
      if (!window.turnstile) {
        await new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
          s.async = true;
          s.onload = resolve;
          s.onerror = () => reject(new Error("не удалось загрузить Turnstile"));
          document.head.appendChild(s);
        });
      }

      // 2. Рендерим виджет с тем же sitekey и action, что использует сайт
      const token = await new Promise((resolve, reject) => {
        const holder = document.createElement("div");
        holder.style.cssText =
          "position:fixed;top:12px;right:12px;z-index:2147483647;background:#fff;" +
          "border:1px solid #ddd;border-radius:10px;padding:10px;box-shadow:0 4px 16px rgba(0,0,0,.25);" +
          "font:13px sans-serif";
        holder.textContent = "Проверка безопасности…";
        document.body.appendChild(holder);
        const timer = setTimeout(() => { cleanup(); reject(new Error("вышло время ожидания проверки")); }, 60000);
        let widget;
        const cleanup = () => {
          clearTimeout(timer);
          try { window.turnstile.remove(widget); } catch (e) { /* noop */ }
          holder.remove();
        };
        widget = window.turnstile.render(holder, {
          sitekey: SITEKEY,
          action: "inbox_browser_verification",
          size: "flexible",
          callback: (t) => { cleanup(); resolve(t); },
          "error-callback": (code) => { cleanup(); reject(new Error("ошибка проверки (" + code + ")")); },
          "expired-callback": () => { cleanup(); reject(new Error("токен истёк до получения")); },
          "timeout-callback": () => { cleanup(); reject(new Error("вышло время ожидания проверки")); },
        });
      });

      // 3a. Есть адрес API — обменяем токен на state прямо отсюда
      if (API_BASE) {
        const headers = { "Content-Type": "application/json" };
        if (API_TOKEN) headers["Authorization"] = "Bearer " + API_TOKEN;
        const response = await fetch(API_BASE.replace(/\/+$/, "") + "/api/verify-browser", {
          method: "POST",
          headers,
          body: JSON.stringify({ turnstile_token: token }),
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error("API вернул " + response.status + ": " + JSON.stringify(payload.error || payload));
        }
        const body = JSON.stringify({ state: payload.state }, null, 2);
        const status = await copyToClipboard(body);
        log("✔", "Готово! В буфере обмена готовый state (хватит примерно на сутки).");
        log("→", "Используйте его как тело запроса: { \"state\": ... } в /api/generate, /api/list и т.д.");
        console.log(payload.state);
        console.log(status);
        return;
      }

      // 3b. Иначе просто копируем токен
      const status = await copyToClipboard(token);
      log("✔", "Токен верификации получен и " + status + " (длина " + token.length + ").");
      log("→", "Сразу отправьте его в API: POST /api/verify-browser с телом {\"turnstile_token\": \"...\"}");
      log("→", "Токен одноразовый и живёт ~5 минут.");
      console.log(token);
    } catch (e) {
      console.error("✖ " + (e && e.message ? e.message : e));
    }
  })();
})();
