db = db.getSiblingDB('shrimpwatch');

// Drop the old user if present (avoids a user-already-exists error on re-runs)
try { db.dropUser('shrimp_app'); print("Dropped old shrimp_app user"); } catch(e) { print("Old user not exist, skip drop: " + e.message); }

// Create a new user with the DBOWNER role (enough for everything the hackathon needs)
db.createUser({
  user: 'shrimp_app',
  pwd: 'shrimp_pass',
  roles: [
    { role: 'dbOwner', db: 'shrimpwatch' },
    { role: 'readAnyDatabase', db: 'admin' }
  ]
});
print('Created shrimp_app user with dbOwner role on shrimpwatch DB');

// Read-only user for Datadog Database Monitoring (datadog/conf.d/mongo.d/conf.yaml): server and collection stats,
// query samples and explain plans. Local container only, like shrimp_app.
const admin = db.getSiblingDB('admin');
try { admin.dropUser('datadog'); } catch (e) { print("No old datadog user, skip drop: " + e.message); }
admin.createUser({
  user: 'datadog',
  pwd: 'datadog_pass',
  roles: [
    { role: 'read', db: 'admin' },
    { role: 'read', db: 'local' },
    { role: 'clusterMonitor', db: 'admin' },
    { role: 'readAnyDatabase', db: 'admin' }
  ]
});
print('Created datadog monitoring user');
