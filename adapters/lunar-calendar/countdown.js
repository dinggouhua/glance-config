const http = require('http');
const { Lunar, HolidayUtil } = require('/opt/lunar-calendar/node_modules/lunar-javascript');

function nextSolar(month, day, now) {
  let d = new Date(Date.UTC(now.getUTCFullYear(), month - 1, day));
  if (d <= now) d = new Date(Date.UTC(now.getUTCFullYear() + 1, month - 1, day));
  return d;
}

function nextLunar(month, day, now) {
  let year = now.getUTCFullYear();
  let d = Lunar.fromYmd(year, month, day).getSolar();
  let result = new Date(Date.UTC(d.getYear(), d.getMonth() - 1, d.getDay()));
  if (result <= now) {
    d = Lunar.fromYmd(year + 1, month, day).getSolar();
    result = new Date(Date.UTC(d.getYear(), d.getMonth() - 1, d.getDay()));
  }
  return result;
}

function item(name, calendar, month, day, now) {
  const date = calendar === 'lunar' ? nextLunar(month, day, now) : nextSolar(month, day, now);
  const days = Math.ceil((date - now) / 86400000);
  return { name, calendar, month, day, date: date.toISOString().slice(0, 10), days };
}

function holidayItems(now) {
  const year = now.getFullYear();
  const month = now.getMonth();
  const days = new Date(year, month + 1, 0).getDate();
  const items = [];
  for (let day = 1; day <= days; day++) {
    const date = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const holiday = HolidayUtil.getHoliday(date);
    if (holiday) {
      items.push({ date, name: holiday.getName(), work: holiday.isWork() });
    }
  }
  return items;
}

const server = http.createServer((req, res) => {
  const now = new Date();
  const payload = req.url === '/holidays'
    ? { year: now.getFullYear(), month: now.getMonth() + 1, items: holidayItems(now) }
    : { items: [
    item('儿子生日', 'solar', 12, 19, now),
    item('结婚纪念日', 'solar', 11, 28, now),
    item('老婆生日', 'lunar', 6, 23, now),
    item('我的生日', 'lunar', 3, 19, now),
    ] };
  const body = JSON.stringify(payload, null, 0);
  res.writeHead(200, {'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body)});
  res.end(body);
});
server.listen(8898, '127.0.0.1');
