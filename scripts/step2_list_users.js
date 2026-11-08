// Use getUsers on the ADMIN db (has admin rights) and filter by the shrimpwatch db
db = db.getSiblingDB("admin");
const usersCursor = db.system.users.find({db: "shrimpwatch"}, {user:1, db:1, roles:1});
const users = usersCursor.toArray();
print("Users on 'shrimpwatch' DB: " + users.length);
users.forEach(u => {
  const roles = (u.roles || []).map(r => r.role + "@" + r.db).join(", ");
  print("- user: " + u.user + "  roles: " + roles);
});
if (users.length === 0) {
  print("!! WARNING: chua co user nao tren DB shrimpwatch. Hay chac chan da: `docker compose down -v && docker compose up -d` de tao lai voi mongo-init.js");
}
