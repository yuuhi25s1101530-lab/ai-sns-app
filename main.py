import sqlite3
import os
import shutil
import bcrypt
from fastapi import FastAPI, HTTPException, status, File, UploadFile, Form
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="難易度MAX高校生SNS API")
DB_NAME = "sns.db"

# ⚠️ 画像保存用フォルダの設定（app = FastAPI() の下に書く必要があります）
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- 1. データベースの初期化 ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER,
            following_id INTEGER,
            PRIMARY KEY (follower_id, following_id),
            FOREIGN KEY (follower_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (following_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER,
            post_id INTEGER,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        );
        """)
        
        # すでに古いpostsテーブルがある場合、image_urlカラムを自動追加
        try:
            cursor.execute("ALTER TABLE posts ADD COLUMN image_url TEXT")
        except sqlite3.OperationalError:
            pass
            
        conn.commit()

init_db()

# --- 2. パスワード暗号化 ---
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

# --- 3. データ定義 ---
class UserAuth(BaseModel):
    username: str
    password: str

class FollowRequest(BaseModel):
    follower_id: int
    following_id: int

class LikeRequest(BaseModel):
    user_id: int
    post_id: int

# --- 4. APIエンドポイント ---

@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: UserAuth):
    hashed = hash_password(user.password)
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (user.username, hashed))
            conn.commit()
        return {"message": "ユーザー登録が成功しました", "username": user.username}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="このユーザー名は既に存在します")

@app.post("/login")
def login(user: UserAuth):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
        db_user = cursor.fetchone()
        
    if db_user is None or not check_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが間違っています")
        
    return {"message": "ログイン成功！", "user_id": db_user["id"], "username": db_user["username"]}

@app.post("/posts", status_code=status.HTTP_201_CREATED)
async def create_post_with_image(
    user_id: int = Form(...),
    content: str = Form(...),
    image: UploadFile = File(None)
):
    """【画像付き投稿API】画像とテキストを同時に保存する"""
    image_url = None
    
    if image and image.filename:
        file_path = os.path.join(UPLOAD_DIR, image.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        image_url = f"/static/uploads/{image.filename}"

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO posts (user_id, content, image_url) VALUES (?, ?, ?)",
            (user_id, content, image_url)
        )
        conn.commit()
        
    return {"message": "投稿が完了しました！"}

@app.post("/follow")
def follow_user(follow: FollowRequest):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO follows (follower_id, following_id) VALUES (?, ?)", (follow.follower_id, follow.following_id))
            conn.commit()
        return {"message": "フォローしました"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="既にフォローしているか、ユーザーが存在しません")

@app.get("/timeline/{user_id}")
def get_timeline(user_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = """
SELECT posts.id, posts.content, posts.image_url, posts.created_at, users.username 
FROM posts
INNER JOIN users ON posts.user_id = users.id
WHERE posts.user_id IN (
    SELECT following_id FROM follows WHERE follower_id = ?
) OR posts.user_id = ?
ORDER BY posts.created_at DESC
        """
        cursor.execute(query, (user_id, user_id))
        timeline = cursor.fetchall()
    return [dict(row) for row in timeline]

@app.post("/likes")
def like_post(like: LikeRequest):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", (like.user_id, like.post_id))
            conn.commit()
        return {"message": "いいねしました！"}
    except sqlite3.IntegrityError:
        return {"message": "既にいいねしています"}

@app.get("/recommend/{user_id}")
def get_recommendations(user_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT content FROM posts 
            INNER JOIN likes ON posts.id = likes.post_id 
            WHERE likes.user_id = ?
        """, (user_id,))
        liked_posts = cursor.fetchall()
        
        user_interests = {}
        for post in liked_posts:
            words = post["content"].split()
            for word in words:
                if len(word) > 1:
                    user_interests[word] = user_interests.get(word, 0) + 1
                    
        cursor.execute("SELECT posts.id, posts.content, posts.image_url, posts.created_at, users.username FROM posts INNER JOIN users ON posts.user_id = users.id")
        all_posts = [dict(row) for row in cursor.fetchall()]
        
        scored_posts = []
        for post in all_posts:
            score = 0
            for word, weight in user_interests.items():
                if word in post["content"]:
                    score += weight
            post["recommend_score"] = score
            scored_posts.append(post)
            
        scored_posts.sort(key=lambda x: (x["recommend_score"], x["created_at"]), reverse=True)
        
    return scored_posts

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()