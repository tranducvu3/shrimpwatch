// Buoc 6: chung minh truy van cua worker dung index ghep + bucket noi bo cua Time Series
// Explain cua Time Series long trong stages[].$cursor -> tim theo ten khoa tren chuoi JSON
function findKey(obj, key) {
  const m = JSON.stringify(obj).match(new RegExp(`"${key}":"?([^",}]+)`));
  return m ? m[1] : undefined;
}

const since = new Date(Date.now() - 5 * 60 * 1000);
const plan = db.metrics
  .find({ "metadata.pond_id": "pond-01", "metadata.metric": "do", timestamp: { $gte: since } })
  .sort({ timestamp: -1 })
  .explain("executionStats");
print("Truy van: DO cua pond-01 trong 5 phut gan nhat");
print("  index su dung       :", findKey(plan, "indexName"));
print("  bucket da doc       :", findKey(plan, "totalDocsExamined"));
print("  so ban ghi tra ve   :", db.metrics.countDocuments({ "metadata.pond_id": "pond-01", "metadata.metric": "do", timestamp: { $gte: since } }));

try {
  const ts = db.metrics.aggregate([{ $collStats: { storageStats: {} } }]).next().storageStats.timeseries;
  print("Time Series = bucket pattern do MongoDB tu quan ly:");
  print("  so bucket           :", ts.bucketCount);
  print("  ban ghi / bucket    :", (db.metrics.countDocuments({}) / Math.max(ts.bucketCount, 1)).toFixed(1));
} catch (e) {
  print("Khong doc duoc $collStats (quyen han che tren cluster nay):", e.message);
}

print("Rollup theo gio (metrics_hourly):", db.metrics_hourly.countDocuments({}), "dong");
