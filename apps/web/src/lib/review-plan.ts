// Allocate explicit selections in the browser's local calendar; no stored plan or inference.
export function reviewPlan(count: number, start: string, perDay: number, interval: number): string[] {
  if (!Number.isInteger(count) || count < 1 || count > 20
    || !Number.isInteger(perDay) || perDay < 1 || perDay > 20
    || !Number.isInteger(interval) || interval < 1 || interval > 30) throw new Error("每天1–20篇，间隔1–30天，选择1–20篇。");
  if (Math.floor((count - 1) / perDay) * interval > 90) throw new Error("计划跨度最多90个日历天，请增加每日篇数或缩短间隔。");
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(start);
  if (!parts) throw new Error("请填写有效的计划开始时间。");
  const [, year, month, day, hour, minute] = parts.map(Number);
  if (year < 1970 || year > 9998) throw new Error("计划年份须在1970–9998之间。");
  const first = new Date(year, month - 1, day, hour, minute);
  if (first.getFullYear() !== year || first.getMonth() !== month - 1 || first.getDate() !== day
    || first.getHours() !== hour || first.getMinutes() !== minute) throw new Error("开始时间无效，或处于本机时区跳过的时刻。");
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(first);
    date.setDate(first.getDate() + Math.floor(index / perDay) * interval);
    if (!Number.isFinite(date.getTime()) || date.getFullYear() > 9999
      || date.getHours() !== hour || date.getMinutes() !== minute) throw new Error("计划遇到时区跳过的时刻，请调整开始时间。");
    return date.toISOString();
  });
}
