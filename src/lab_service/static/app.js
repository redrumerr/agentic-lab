'use strict';
// Все токены и состояние панели живут только в текущей вкладке.
// Короткий доступ к элементу формы по id.
const $ = id => document.getElementById(id);
let attack = null,
  history = [],
  focused = null,
  generation = 0,
  logText = '',
  submitting = false,
  session = 0,
  selection = 0;
// session меняется при подключении, selection — при выборе сценария,
// generation — при выборе журнала. Поздний ответ старого запроса игнорируется.
const labels = {
  up: 'Поднять / сбросить VM',
  down: 'Удалить VM',
  verify: 'Проверка',
  sandbox: 'Sandbox',
  evaluator: 'Evaluator',
  status: 'Статус VM',
  logs: 'Логи контейнеров'
};
const names = {
  'context-poisoning': 'Подмена контекста',
  'tool-misuse': 'Злоупотребление tool',
  'privilege-boundary': 'Граница привилегий',
  'memory-poisoning': 'Отравление памяти',
  'message-poisoning': 'Подмена сообщений'
};
// Читает текущий токен роли из формы без сохранения в localStorage.
const token = role => $(role + '-token').value.trim();
// Пауза между запросами polling.
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// Показывает сообщение оператора или скрывает пустое уведомление.
function notice(message = '') {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}
/**
 * Выполняет запрос API с токеном выбранной роли.
 * Токен берётся из формы непосредственно перед запросом, а не из постоянного
 * хранилища. Ответы HTTP с ошибкой преобразуются в понятное сообщение панели.
 */
async function api(path, role = 'evaluator', options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Lab-Token': token(role)
    },
    cache: 'no-store'
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error('Сервер вернул некорректный ответ');
  }
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}

// Включает действия только при наличии сценария, нужного токена и свободной VM.
function controls() {
  const busy = submitting || history.some(j => j.status === 'running');
  document.querySelectorAll('[data-action]').forEach(button => {
    const action = button.dataset.action;
    let role = 'evaluator';
    if (action === 'prompt') role = 'sandbox';
    if (action === 'exec') role = $('channel').value;
    button.disabled = !attack || !token(role) || busy || (action === 'submit' && attack.mode !== 'escape');
  });
}

// Очищает только отображаемый журнал; выполняющаяся задача остаётся на сервере.
function clearLog() {
  logText = '';
  $('log').textContent = '';
  $('log-size').textContent = '0 символов';
}

/**
 * Добавляет блок журнала и ограничивает объём отображаемого текста.
 * ANSI-коды удаляются. textContent сохраняет вывод команд как обычный текст,
 * поэтому строка из sandbox с HTML или script не исполняется браузером.
 */
function appendLog(stream, text) {
  text = text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  logText += text;
  if (logText.length > 200000) {
    logText = logText.slice(-160000);
    $('log').textContent = '[Показан конец журнала]\n' + logText;
  } else {
    const span = document.createElement('span');
    span.className = stream;
    span.textContent = text;
    $('log').append(span);
  }
  $('log-size').textContent = logText.length.toLocaleString('ru') + ' символов';
  if ($('autoscroll').checked) $('log').scrollTop = $('log').scrollHeight;
}

// Показывает общий verdict и actual/expected/error для каждой проверки.
function showChecks(result) {
  $('verdict').textContent = result.passed ? 'Условия выполнены' : 'Условия не выполнены';
  $('verdict').className = result.passed ? 'ok' : 'warn';
  $('semantics').textContent = result.semantics || '';
  $('checks').textContent = '';
  for (const check of result.checks) {
    const row = document.createElement('div');
    row.className = 'check';
    const title = document.createElement('strong');
    title.className = check.passed ? 'ok' : check.error ? 'fail' : 'warn';
    title.textContent = (check.passed ? '✓ ' : check.error ? '! ' : '○ ') + check.name;
    const values = document.createElement('pre');
    values.textContent = 'Ожидалось: ' + JSON.stringify(check.expected) + '\nПолучено: ' + JSON.stringify(check.actual) + (check.error ? '\nОшибка: ' + check.error : '');
    row.append(title, values);
    $('checks').append(row);
  }
}

// Перерисовывает историю; нажатие на строку переключает наблюдаемый журнал.
function drawHistory() {
  $('history').textContent = '';
  if (!history.length) {
    $('history').textContent = 'Пока нет операций';
    controls();
    return;
  }
  history.forEach(job => {
    const button = document.createElement('button');
    button.className = 'history-row';
    const fields = [new Date(job.created_at).toLocaleTimeString('ru'), labels[job.action], job.attack_id, job.status === 'running' ? 'Выполняется' : job.status === 'failed' ? 'Ошибка' : 'Завершено'];
    fields.forEach((text, i) => {
      const span = document.createElement('span');
      span.textContent = text;
      if (i === 3) span.className = job.status === 'failed' ? 'fail' : job.status === 'running' ? 'warn' : 'ok';
      button.append(span);
    });
    button.onclick = () => watch(job);
    $('history').append(button);
  });
  controls();
}
/**
 * Получает историю обеих подключённых ролей и объединяет её по времени.
 * После await проверяем session: ответ прежнего подключения не должен вернуть
 * историю в уже отключённую или заново подключённую вкладку.
 */
async function refreshHistory() {
  const epoch = session;
  const roles = ['evaluator', 'sandbox'].filter(role => token(role));
  const requests = roles.map(async role => {
    const jobs = await api('/v1/jobs', role);
    return jobs.map(job => ({ ...job, role }));
  });
  const lists = await Promise.all(requests);
  if (epoch !== session) return;
  history = lists.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
  drawHistory();
}
/**
 * Читает события выбранной задачи начиная с курсора after.
 * generation останавливает обновление старого журнала при переключении задачи
 * или отключении. Это останавливает только polling вкладки; сама операция
 * продолжает работать в Jobs на сервере. При завершении показываем результат.
 */
async function watch(job) {
  const epoch = ++generation;
  focused = job;
  clearLog();
  $('log-title').textContent = labels[job.action] + ' · ' + job.attack_id;
  let after = 0;
  try {
    while (epoch === generation) {
      const data = await api('/v1/jobs/' + job.id + '?after=' + after, job.role);
      if (epoch !== generation) return;
      if (data.truncated) appendLog('system', '[Начало журнала больше не хранится на сервере]\n');
      for (const event of data.events) appendLog(event.stream, event.text);
      after = data.cursor;
      $('job-state').textContent = data.status === 'running' ? 'Выполняется…' : data.status === 'failed' ? 'Ошибка' : 'Завершено';
      const item = history.find(j => j.id === job.id);
      if (item) item.status = data.status;
      drawHistory();
      if (data.status !== 'running') {
        if (data.error) appendLog('stderr', data.error + '\n');
        if (data.result?.checks) {
          showChecks(data.result);
          $('semantics').textContent = job.attack_id + ' · ' + (data.result.semantics || '');
        }
        if (data.result && 'exit_code' in data.result) appendLog('system', '\nКод завершения: ' + data.result.exit_code + (data.result.timed_out ? ' · превышен таймаут' : '') + '\n');
        await refreshHistory();
        return;
      }
      await delay(650);
    }
  } catch (error) {
    if (epoch === generation) {
      notice(error.message + ' · Операция может продолжаться на сервере. Обновите историю.');
      $('job-state').textContent = 'Связь прервана';
    }
  }
}
// Загружает сценарий и отбрасывает ответ, если оператор уже выбрал другой ID.
async function selectAttack(id) {
  const epoch = ++selection;
  try {
    const data = await api('/v1/attacks/' + id);
    if (epoch !== selection) return;
    attack = data;
    $('flag-form').hidden = data.mode !== 'escape';
    $('flag').value = '';
    $('attack-id').textContent = data.id;
    $('attack-title').textContent = names[data.id] || data.title;
    $('attack-category').textContent = data.category;
    $('prompt').disabled = false;
    $('prompt').value = data.prompt;
    $('checks').textContent = '';
    $('verdict').textContent = 'Ожидает проверки';
    $('verdict').className = '';
    $('semantics').textContent = data.metadata.attack_success;
    document.querySelectorAll('.scenario-link').forEach(b => b.classList.toggle('active', b.dataset.id === id));
    controls();
  } catch (error) {
    notice(error.message);
  }
}
// Подключение создаёт новую session и получает каталог с evaluator-токеном.
$('connect-form').onsubmit = async event => {
  event.preventDefault();
  session++;
  generation++;
  notice();
  try {
    const list = await api('/v1/attacks');
    $('scenarios').textContent = '';
    $('scenario-count').textContent = String(list.length).padStart(2, '0');
    list.forEach((scenario, i) => {
      const button = document.createElement('button');
      button.className = 'scenario-link';
      button.dataset.id = scenario.id;
      const num = document.createElement('span');
      num.className = 'number';
      num.textContent = String(i + 1).padStart(2, '0');
      const name = document.createElement('span');
      name.textContent = names[scenario.id] || scenario.title;
      button.append(num, name);
      button.onclick = () => selectAttack(scenario.id);
      $('scenarios').append(button);
    });
    $('connection-state').textContent = 'Подключено · токены только в этой вкладке';
    $('connection').open = false;
    await selectAttack(attack?.id || list[0]?.id);
    await refreshHistory();
    const active = history.find(j => j.status === 'running');
    if (active) watch(active);
  } catch (error) {
    notice(error.message);
    $('connection').open = true;
  }
};
// Отключение инвалидирует ожидаемые ответы и очищает токены и данные формы.
$('disconnect').onclick = () => {
  generation++;
  session++;
  selection++;
  attack = null;
  history = [];
  focused = null;
  ['evaluator-token', 'sandbox-token'].forEach(id => $(id).value = '');
  $('connection-state').textContent = 'Отключено';
  $('connection').open = true;
  $('scenarios').textContent = 'Подключитесь для просмотра сценариев';
  $('scenario-count').textContent = '00';
  $('attack-title').textContent = 'Выберите эксперимент';
  $('attack-id').textContent = 'Не выбран';
  $('attack-category').textContent = '';
  $('flag-form').hidden = true;
  $('flag').value = '';
  $('prompt').value = '';
  $('prompt').disabled = true;
  $('checks').textContent = '';
  $('verdict').textContent = 'Ожидает проверки';
  $('semantics').textContent = '';
  $('job-state').textContent = 'Нет активной операции';
  clearLog();
  drawHistory();
  notice();
};
/**
 * Все кнопки действий проходят через один обработчик.
 * Submission флага выполняется отдельным запросом. Остальные действия создают
 * фоновую задачу; её ID передаётся watch. Prompt кодируется в Base64 до отправки
 * команды, чтобы его кавычки и переводы строк не меняли shell-обёртку.
 */
document.querySelectorAll('[data-action]').forEach(button => button.onclick = async () => {
  let action = button.dataset.action;
  notice();
  if (!attack) return;
  if (action === 'submit') {
    if (!/^flag\{[a-zA-Z0-9_-]+\}$/.test($('flag').value)) {
      notice('Введите флаг вида flag{…}');
      return;
    }
    submitting = true;
    controls();
    try {
      const result = await api('/v1/attacks/' + attack.id + '/submit', 'evaluator', {
        method: 'POST',
        body: JSON.stringify({
          flag: $('flag').value
        })
      });
      showChecks(result);
      $('flag').value = '';
    } catch (error) {
      notice(error.message);
    } finally {
      submitting = false;
      controls();
    }
    return;
  }
  if ((action === 'down' || action === 'up') && !confirm(action === 'down' ? 'Удалить лабораторную VM и её данные?' : attack.mode === 'escape' ? 'Создать чистую VM для escape-сценария? Существующая лабораторная VM и её данные будут удалены.' : 'Поднять VM? Если она уже существует, БД и память sandbox будут сброшены.')) return;
  let body;
  if (action === 'exec') {
    action = $('channel').value;
    body = {
      command: $('command').value,
      timeout_seconds: Number($('timeout').value)
    };
  }
  if (action === 'prompt') {
    action = 'sandbox';
    const bytes = new TextEncoder().encode($('prompt').value);
    let binary = '';
    bytes.forEach(byte => binary += String.fromCharCode(byte));
    const encoded = btoa(binary);
    body = {
      command: 'printf %s ' + encoded + ' | base64 -d > /state/ui-prompt.txt && python3 /app/adapter.py --prompt-file /state/ui-prompt.txt',
      timeout_seconds: attack.mode === 'escape' ? 7200 : 300
    };
  }
  if (body && (!body.command || !Number.isInteger(body.timeout_seconds) || body.timeout_seconds < 1 || body.timeout_seconds > 7200)) {
    notice('Введите команду и целый таймаут от 1 до 7200 секунд');
    return;
  }
  const targetId = attack.id;
  submitting = true;
  controls();
  try {
    const role = action === 'sandbox' ? 'sandbox' : 'evaluator';
    const result = await api('/v1/attacks/' + targetId + '/jobs/' + action, role, {
      method: 'POST',
      ...(body ? {
        body: JSON.stringify(body)
      } : {})
    });
    const job = {
      id: result.id,
      action,
      attack_id: targetId,
      role,
      status: 'running',
      created_at: new Date().toISOString()
    };
    history.unshift(job);
    drawHistory();
    watch(job);
  } catch (error) {
    notice(error.message);
  } finally {
    submitting = false;
    controls();
  }
});
$('channel').onchange = () => {
  $('channel-hint').textContent = $('channel').value === 'sandbox' ? 'Команда выполняется внутри выбранного sandbox-контейнера.' : 'Evaluator имеет полный доступ к VM. Этот канал предназначен для оператора.';
  controls();
};
$('clear-log').onclick = clearLog;
$('refresh-history').onclick = () => refreshHistory().catch(error => notice(error.message));
$('download').onclick = () => {
  const url = URL.createObjectURL(new Blob([logText], {
    type: 'text/plain;charset=utf-8'
  }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'lab-' + (focused?.id || 'log') + '.txt';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
fetch('/healthz').then(r => {
  if (!r.ok) throw Error();
  $('health').textContent = '● API доступен';
}).catch(() => {
  $('health').textContent = 'API недоступен';
});

setInterval(() => {
  if (token('evaluator') && history.some(j => j.status === 'running')) refreshHistory().catch(() => {});
}, 3000);
