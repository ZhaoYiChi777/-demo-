"""Authenticated fixtures for existing business tests; demo seeding is not an API privilege."""
import main


def login(client, admin=False):
    response = client.post('/api/auth/login', json={'username': 'manner' if admin else 'user1',
                                                  'password': '00000000' if admin else '12345678'})
    assert response.status_code == 200, response.text


def seed_demo(client):
    demo = main.workbench.demo()
    with main.db() as conn:
        conn.execute('INSERT OR REPLACE INTO project_owners VALUES(?,?)', (demo['project_id'], 'user1'))
    login(client)
    return demo
