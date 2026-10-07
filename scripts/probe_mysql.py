import pymysql

hosts = ["127.0.0.1", "localhost"]
ports = [3306, 33060, 3307, 3308, 3309]
users = ["root"]
passwords = ["123456"]
found = []

for host in hosts:
    for port in ports:
        for user in users:
            for pw in passwords:
                try:
                    conn = pymysql.connect(
                        host=host, port=port, user=user, password=pw,
                        connect_timeout=3,
                    )
                    cur = conn.cursor()
                    cur.execute("SHOW DATABASES")
                    dbs = [r[0] for r in cur.fetchall()]
                    print(f"[OK] {user}@{host}:{port}  dbs={dbs}")
                    if "agent" in dbs:
                        found.append((host, port, user, pw))
                        print(f"    >>> 'agent' 库存在！")
                    conn.close()
                except pymysql.OperationalError as e:
                    err = str(e)
                    if "Access denied" in err:
                        # 账号存在但密码/主机不对，记录一下
                        print(f"[DENIED] {user}@{host}:{port} -> {err.split(chr(10))[0]}")
                    # 其他错误（连接失败）静默
                except Exception as e:
                    pass

print("\n=== 含 agent 库的实例 ===")
for f in found:
    print(f)
if not found:
    print("未在任何 TCP 端口找到含 'agent' 库的实例（可能是 SSH 隧道/远程/非标准端口）")
