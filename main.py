import sqlite3
import feedparser
import time
import threading
import re
from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime
import os
from flask_restx import Api, Resource, fields

app = Flask(__name__)
api = Api(app, version='1.0', title='RSS Monitor API',
    description='API для мониторинга RSS-лент и управления ключевыми словами')

news_model = api.model('News', {
    'id': fields.Integer(description='ID новости'),
    'title': fields.String(description='Заголовок новости'),
    'content': fields.String(description='Содержание новости'),
    'url': fields.String(description='URL новости'),
    'published_date': fields.String(description='Дата публикации'),
    'found_date': fields.String(description='Дата обнаружения'),
    'source_name': fields.String(description='Название источника'),
    'keywords': fields.List(fields.String, description='Ключевые слова')
})

source_model = api.model('Source', {
    'id': fields.Integer(description='ID источника'),
    'name': fields.String(description='Название источника'),
    'url': fields.String(description='URL RSS-ленты'),
    'active': fields.Boolean(description='Статус активности')
})

keyword_model = api.model('Keyword', {
    'id': fields.Integer(description='ID ключевого слова'),
    'word': fields.String(description='Ключевое слово'),
    'active': fields.Boolean(description='Статус активности')
})

news_ns = api.namespace('news', description='Операции с новостями')
sources_ns = api.namespace('sources', description='Операции с источниками')
keywords_ns = api.namespace('keywords', description='Операции с ключевыми словами')

DB_PATH = "rss_monitor.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        active INTEGER DEFAULT 1
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        word TEXT NOT NULL UNIQUE,
        active INTEGER DEFAULT 1
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT,
        url TEXT NOT NULL UNIQUE,
        source_id INTEGER,
        published_date TEXT,
        found_date TEXT,
        FOREIGN KEY (source_id) REFERENCES sources (id)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news_keywords (
        news_id INTEGER,
        keyword_id INTEGER,
        PRIMARY KEY (news_id, keyword_id),
        FOREIGN KEY (news_id) REFERENCES news (id),
        FOREIGN KEY (keyword_id) REFERENCES keywords (id)
    )
    ''')
    
    conn.commit()
    conn.close()

def fetch_rss_news():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id, url FROM sources WHERE active = 1")
            sources = cursor.fetchall()
            
            cursor.execute("SELECT id, word FROM keywords WHERE active = 1")
            keywords = cursor.fetchall()
            keyword_patterns = [(keyword_id, re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)) 
                               for keyword_id, word in keywords]
            
            for source_id, source_url in sources:
                try:
                    feed = feedparser.parse(source_url)
                    
                    for entry in feed.entries:
                        title = entry.get('title', '')
                        content = entry.get('description', '') or entry.get('summary', '')
                        link = entry.get('link', '')
                        published = entry.get('published', '') or entry.get('pubDate', '')

                        cursor.execute("SELECT id FROM news WHERE url = ?", (link,))
                        if cursor.fetchone() is not None:
                            continue
                        
                        matched_keywords = []
                        for keyword_id, pattern in keyword_patterns:
                            if pattern.search(title) or pattern.search(content):
                                matched_keywords.append(keyword_id)
                        
                        if matched_keywords:
                            try:
                                cursor.execute('''
                                INSERT INTO news (title, content, url, source_id, published_date, found_date)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ''', (title, content, link, source_id, published, datetime.now().isoformat()))
                                conn.commit()

                                cursor.execute("SELECT id FROM news WHERE url = ?", (link,))
                                news_id = cursor.fetchone()[0]

                                for keyword_id in matched_keywords:
                                    cursor.execute('''
                                    INSERT INTO news_keywords (news_id, keyword_id)
                                    VALUES (?, ?)
                                    ''', (news_id, keyword_id))
                                
                                conn.commit()

                                cursor.execute("SELECT word FROM keywords WHERE id IN ({})".format(
                                    ','.join('?' * len(matched_keywords))), matched_keywords)
                                found_keywords = [row[0] for row in cursor.fetchall()]
                                
                                print(f"[{datetime.now()}] НОВАЯ НОВОСТЬ: {title}")
                                print(f"    URL: {link}")
                                print(f"    Найдены ключевые слова: {', '.join(found_keywords)}")
                                print("-" * 80)
                            except sqlite3.IntegrityError:
                                pass
                except Exception as e:
                    print(f"[{datetime.now()}] Ошибка при обработке источника {source_url}: {e}")
            
            conn.close()
        except Exception as e:
            print(f"[{datetime.now()}] Ошибка в процессе сбора новостей: {e}")

        time.sleep(300)


@app.route('/')
def index():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT n.id, n.title, n.content, n.url, n.published_date, n.found_date, s.name as source_name
    FROM news n
    JOIN sources s ON n.source_id = s.id
    ORDER BY n.found_date DESC
    LIMIT 50
    ''')
    news = [dict(row) for row in cursor.fetchall()]
    
    for item in news:
        cursor.execute('''
        SELECT k.word
        FROM keywords k
        JOIN news_keywords nk ON k.id = nk.keyword_id
        WHERE nk.news_id = ?
        ''', (item['id'],))
        keywords = [row[0] for row in cursor.fetchall()]
        item['keywords'] = ', '.join(keywords)
    
    conn.close()
    
    return render_template('index.html', news=news)

@app.route('/sources')
def sources():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM sources ORDER BY name")
    sources = cursor.fetchall()
    
    conn.close()
    
    return render_template('sources.html', sources=sources)

@app.route('/sources/add', methods=['POST'])
def add_source():
    name = request.form.get('name')
    url = request.form.get('url')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("INSERT INTO sources (name, url) VALUES (?, ?)", (name, url))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    
    conn.close()
    
    return redirect(url_for('sources'))

@app.route('/sources/delete/<int:source_id>', methods=['POST'])
def delete_source(source_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    
    conn.close()
    
    return redirect(url_for('sources'))

@app.route('/sources/toggle/<int:source_id>', methods=['POST'])
def toggle_source(source_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("UPDATE sources SET active = 1 - active WHERE id = ?", (source_id,))
    conn.commit()
    
    conn.close()
    
    return redirect(url_for('sources'))

@app.route('/keywords')
def keywords():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM keywords ORDER BY word")
    keywords = cursor.fetchall()
    
    conn.close()
    
    return render_template('keywords.html', keywords=keywords)

@app.route('/keywords/add', methods=['POST'])
def add_keyword():
    word = request.form.get('word')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("INSERT INTO keywords (word) VALUES (?)", (word,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    
    conn.close()
    
    return redirect(url_for('keywords'))

@app.route('/keywords/delete/<int:keyword_id>', methods=['POST'])
def delete_keyword(keyword_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
    conn.commit()
    
    conn.close()
    
    return redirect(url_for('keywords'))

@app.route('/keywords/toggle/<int:keyword_id>', methods=['POST'])
def toggle_keyword(keyword_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("UPDATE keywords SET active = 1 - active WHERE id = ?", (keyword_id,))
    conn.commit()
    
    conn.close()
    
    return redirect(url_for('keywords'))

# API эндпоинты 
@news_ns.route('/')
class NewsList(Resource):
    @news_ns.doc('get_news',
        params={
            'keyword': 'Фильтр по ключевому слову',
            'source': 'Фильтр по источнику'
        })
    @news_ns.marshal_list_with(news_model)
    def get(self):
        """Получить список новостей с возможностью фильтрации"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        keyword = request.args.get('keyword')
        source = request.args.get('source')
        
        query = '''
        SELECT n.id, n.title, n.content, n.url, n.published_date, n.found_date, s.name as source_name
        FROM news n
        JOIN sources s ON n.source_id = s.id
        '''
        
        params = []
        conditions = []
        
        if keyword:
            query += '''
            JOIN news_keywords nk ON n.id = nk.news_id
            JOIN keywords k ON nk.keyword_id = k.id
            '''
            conditions.append("k.word LIKE ?")
            params.append(f"%{keyword}%")
        
        if source:
            conditions.append("s.name LIKE ?")
            params.append(f"%{source}%")
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY n.found_date DESC LIMIT 100"
        
        cursor.execute(query, params)
        news = [dict(row) for row in cursor.fetchall()]

        for item in news:
            cursor.execute('''
            SELECT k.word
            FROM keywords k
            JOIN news_keywords nk ON k.id = nk.keyword_id
            WHERE nk.news_id = ?
            ''', (item['id'],))
            keywords = [row[0] for row in cursor.fetchall()]
            item['keywords'] = keywords
        
        conn.close()
        
        return news

@sources_ns.route('/')
class SourceList(Resource):
    @sources_ns.doc('get_sources')
    @sources_ns.marshal_list_with(source_model)
    def get(self):
        """Получить список всех источников"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM sources ORDER BY name")
        sources = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return sources

    @sources_ns.doc('create_source')
    @sources_ns.expect(source_model)
    @sources_ns.marshal_with(source_model, code=201)
    def post(self):
        """Добавить новый источник"""
        data = request.json
        name = data.get('name')
        url = data.get('url')
        
        if not name or not url:
            api.abort(400, "Требуются поля name и url")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        try:
            cursor.execute("INSERT INTO sources (name, url) VALUES (?, ?)", (name, url))
            conn.commit()
            source_id = cursor.lastrowid
            conn.close()
            
            return {"id": source_id, "name": name, "url": url, "active": 1}, 201
        except sqlite3.IntegrityError:
            conn.close()
            api.abort(400, "Источник с таким URL уже существует")

@sources_ns.route('/<int:source_id>')
@sources_ns.param('source_id', 'ID источника')
class Source(Resource):
    @sources_ns.doc('delete_source')
    @sources_ns.response(204, 'Источник удален')
    def delete(self, source_id):
        """Удалить источник"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
        conn.close()
        
        return '', 204

@keywords_ns.route('/')
class KeywordList(Resource):
    @keywords_ns.doc('get_keywords')
    @keywords_ns.marshal_list_with(keyword_model)
    def get(self):
        """Получить список всех ключевых слов"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM keywords ORDER BY word")
        keywords = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return keywords

    @keywords_ns.doc('create_keyword')
    @keywords_ns.expect(keyword_model)
    @keywords_ns.marshal_with(keyword_model, code=201)
    def post(self):
        """Добавить новое ключевое слово"""
        data = request.json
        word = data.get('word')
        
        if not word:
            api.abort(400, "Требуется поле word")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        try:
            cursor.execute("INSERT INTO keywords (word) VALUES (?)", (word,))
            conn.commit()
            keyword_id = cursor.lastrowid
            conn.close()
            
            return {"id": keyword_id, "word": word, "active": 1}, 201
        except sqlite3.IntegrityError:
            conn.close()
            api.abort(400, "Такое ключевое слово уже существует")

@keywords_ns.route('/<int:keyword_id>')
@keywords_ns.param('keyword_id', 'ID ключевого слова')
class Keyword(Resource):
    @keywords_ns.doc('delete_keyword')
    @keywords_ns.response(204, 'Ключевое слово удалено')
    def delete(self, keyword_id):
        """Удалить ключевое слово"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
        conn.commit()
        conn.close()
        
        return '', 204

def create_templates():
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html>
<head>
    <title>RSS Монитор</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .nav { display: flex; gap: 20px; }
        .nav a { text-decoration: none; color: #0066cc; }
        .news-item { border: 1px solid #ddd; padding: 15px; margin-bottom: 15px; border-radius: 5px; }
        .news-title { font-size: 18px; margin-top: 0; }
        .news-meta { color: #666; font-size: 14px; margin-bottom: 10px; }
        .news-content { margin-bottom: 10px; }
        .news-content img { width: 100%; height: auto; object-fit: cover; }
        .news-link { color: #0066cc; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>RSS Монитор</h1>
            <div class="nav">
                <a href="/">Новости</a>
                <a href="/sources">Источники</a>
                <a href="/keywords">Ключевые слова</a>
            </div>
        </div>
        
        <h2>Последние новости</h2>
        
        {% if news %}
            {% for item in news %}
                <div class="news-item">
                    <h3 class="news-title">{{ item.title }}</h3>
                    <div class="news-meta">
                        Источник: {{ item.source_name }} | 
                        Опубликовано: {{ item.published_date }} | 
                        Найдено: {{ item.found_date }} |
                        Ключевые слова: {{ item.keywords }}
                    </div>
                    <div class="news-content">{{ item.content | safe }}</div>
                    <a href="{{ item.url }}" target="_blank" class="news-link">Читать источник</a>
                </div>
            {% endfor %}
        {% else %}
            <p>Новостей пока нет. Добавьте источники и ключевые слова.</p>
        {% endif %}
    </div>
</body>
</html>
        ''')
    
    with open('templates/sources.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html>
<head>
    <title>Источники RSS - RSS Монитор</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .nav { display: flex; gap: 20px; }
        .nav a { text-decoration: none; color: #0066cc; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; }
        .form-group input { width: 100%; padding: 8px; box-sizing: border-box; }
        .btn { padding: 8px 16px; background-color: #0066cc; color: white; border: none; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        .action-btn { padding: 5px 10px; margin-right: 5px; background-color: #f44336; color: white; border: none; cursor: pointer; }
        .toggle-btn { padding: 5px 10px; background-color: #4CAF50; color: white; border: none; cursor: pointer; }
        .toggle-btn.inactive { background-color: #ccc; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Управление источниками RSS</h1>
            <div class="nav">
                <a href="/">Новости</a>
                <a href="/sources">Источники</a>
                <a href="/keywords">Ключевые слова</a>
            </div>
        </div>
        
        <h2>Добавить новый источник</h2>
        <form action="/sources/add" method="post">
            <div class="form-group">
                <label for="name">Название:</label>
                <input type="text" id="name" name="name" required>
            </div>
            <div class="form-group">
                <label for="url">URL RSS-ленты:</label>
                <input type="url" id="url" name="url" required>
            </div>
            <button type="submit" class="btn">Добавить</button>
        </form>
        
        <h2>Существующие источники</h2>
        {% if sources %}
            <table>
                <thead>
                    <tr>
                        <th>Название</th>
                        <th>URL</th>
                        <th>Статус</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {% for source in sources %}
                        <tr>
                            <td>{{ source.name }}</td>
                            <td>{{ source.url }}</td>
                            <td>{{ 'Активен' if source.active else 'Неактивен' }}</td>
                            <td>
                                <form action="/sources/delete/{{ source.id }}" method="post" style="display: inline;">
                                    <button type="submit" class="action-btn">Удалить</button>
                                </form>
                                <form action="/sources/toggle/{{ source.id }}" method="post" style="display: inline;">
                                    <button type="submit" class="toggle-btn {{ 'inactive' if source.active else '' }}">
                                        {{ 'Деактивировать' if source.active else 'Активировать' }}
                                    </button>
                                </form>
                            </td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% else %}
            <p>Источников пока нет.</p>
        {% endif %}
    </div>
</body>
</html>
        ''')
    
    with open('templates/keywords.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html>
<head>
    <title>Ключевые слова - RSS Монитор</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .nav { display: flex; gap: 20px; }
        .nav a { text-decoration: none; color: #0066cc; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; }
        .form-group input { width: 100%; padding: 8px; box-sizing: border-box; }
        .btn { padding: 8px 16px; background-color: #0066cc; color: white; border: none; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        .action-btn { padding: 5px 10px; margin-right: 5px; background-color: #f44336; color: white; border: none; cursor: pointer; }
        .toggle-btn { padding: 5px 10px; background-color: #4CAF50; color: white; border: none; cursor: pointer; }
        .toggle-btn.inactive { background-color: #ccc; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Управление ключевыми словами</h1>
            <div class="nav">
                <a href="/">Новости</a>
                <a href="/sources">Источники</a>
                <a href="/keywords">Ключевые слова</a>
            </div>
        </div>
        
        <h2>Добавить новое ключевое слово</h2>
        <form action="/keywords/add" method="post">
            <div class="form-group">
                <label for="word">Ключевое слово:</label>
                <input type="text" id="word" name="word" required>
            </div>
            <button type="submit" class="btn">Добавить</button>
        </form>
        
        <h2>Существующие ключевые слова</h2>
        {% if keywords %}
            <table>
                <thead>
                    <tr>
                        <th>Слово</th>
                        <th>Статус</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {% for keyword in keywords %}
                        <tr>
                            <td>{{ keyword.word }}</td>
                            <td>{{ 'Активно' if keyword.active else 'Неактивно' }}</td>
                            <td>
                                <form action="/keywords/delete/{{ keyword.id }}" method="post" style="display: inline;">
                                    <button type="submit" class="action-btn">Удалить</button>
                                </form>
                                <form action="/keywords/toggle/{{ keyword.id }}" method="post" style="display: inline;">
                                    <button type="submit" class="toggle-btn {{ 'inactive' if keyword.active else '' }}">
                                        {{ 'Деактивировать' if keyword.active else 'Активировать' }}
                                    </button>
                                </form>
                            </td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% else %}
            <p>Ключевых слов пока нет.</p>
        {% endif %}
    </div>
</body>
</html>
        ''')

if __name__ == "__main__":
    init_db()
    
    create_templates()
    
    rss_thread = threading.Thread(target=fetch_rss_news, daemon=True)
    rss_thread.start()
    
    app.run(host='0.0.0.0', port=5000, debug=True)