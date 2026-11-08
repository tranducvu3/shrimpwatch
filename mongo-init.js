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
