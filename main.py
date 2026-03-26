"""StudyMind AI - Backend v15.0 (İyileştirilmiş AI Özet + Batch Test + Çıkmış Sorular)"""
import fitz  # PyMuPDF
import httpx

import os,sqlite3,json,uuid,hashlib,random,string,smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime,timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI,UploadFile,File,Form,HTTPException,Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional,List

try:
    from dotenv import load_dotenv
    load_dotenv()
except:pass

# --- TÜM GİZLİ BİLGİLER .env'DEN OKUNUYOR ---
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")

DB_PATH="studymind.db"
ADMIN_EMAILS=[os.getenv("ADMIN_EMAIL", "serkant.karabulut@hotmail.com")]
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", "StudyMind2026!")

SMTP_SERVER = os.getenv("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_EMAIL = os.getenv("SMTP_USER", "destek@studymindai.com")
SMTP_PASSWORD = os.getenv("SMTP_PASS", "")
# -----------------------------------------------

def hash_pw(pw):return hashlib.sha256(pw.encode()).hexdigest()
ADMIN_HASH=hash_pw(ADMIN_PASSWORD)

def get_db():
    conn=sqlite3.connect(DB_PATH,check_same_thread=False);conn.row_factory=sqlite3.Row
    try:yield conn
    finally:conn.close()

def init_db():
    conn=sqlite3.connect(DB_PATH,check_same_thread=False);c=conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,display_name TEXT,password_hash TEXT,plan TEXT DEFAULT 'free',plan_expires TEXT,streak INTEGER DEFAULT 0,last_study_date TEXT,is_banned INTEGER DEFAULT 0,invite_code TEXT,invited_by TEXT,credits INTEGER DEFAULT 0,university TEXT,department TEXT,reset_code TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS exams(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,exam_type TEXT NOT NULL,exam_date_start TEXT NOT NULL,exam_date_end TEXT,description TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,filename TEXT NOT NULL,original_text TEXT,summary TEXT,key_points TEXT,quiz_data TEXT,status TEXT DEFAULT 'uploaded',created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS quiz_history(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,document_id TEXT NOT NULL,score INTEGER,total INTEGER,percentage REAL,answers_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS study_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,document_id TEXT,duration_minutes INTEGER,quiz_score INTEGER,quiz_total INTEGER,session_date TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,amount REAL NOT NULL,currency TEXT DEFAULT 'TRY',status TEXT DEFAULT 'pending',plan TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS blog_posts(id INTEGER PRIMARY KEY AUTOINCREMENT,title_tr TEXT,title_en TEXT,excerpt_tr TEXT,excerpt_en TEXT,content_tr TEXT,content_en TEXT,category_tr TEXT,category_en TEXT,image TEXT DEFAULT '📝',published INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS site_settings(key TEXT PRIMARY KEY,value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,message TEXT,type TEXT DEFAULT 'info',active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS pricing_plans(id TEXT PRIMARY KEY,name TEXT,price REAL,features_tr TEXT,features_en TEXT,popular INTEGER DEFAULT 0,sort_order INTEGER DEFAULT 0,pdf_limit INTEGER DEFAULT 3,max_file_mb INTEGER DEFAULT 10,monthly_credits INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,user_email TEXT,user_name TEXT,message TEXT NOT NULL,admin_reply TEXT,status TEXT DEFAULT 'open',created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY AUTOINCREMENT,inviter_id TEXT NOT NULL,invitee_id TEXT NOT NULL,reward_given INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS credit_packages(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,credits INTEGER NOT NULL,price REAL NOT NULL,sort_order INTEGER DEFAULT 0,active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS credit_settings(key TEXT PRIMARY KEY,value INTEGER NOT NULL,label_tr TEXT,label_en TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS credit_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,amount INTEGER NOT NULL,type TEXT NOT NULL,description TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,sender_id TEXT NOT NULL,receiver_id TEXT NOT NULL,content TEXT NOT NULL,is_read INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    # v15.3: Telif hakkı bildirimleri tablosu
    c.execute("""CREATE TABLE IF NOT EXISTS copyright_reports(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_name TEXT NOT NULL,
        reporter_email TEXT NOT NULL,
        reporter_role TEXT,
        document_id TEXT,
        document_filename TEXT,
        description TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        admin_notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # v15.3: Bekleme listesi tablosu
    c.execute("""CREATE TABLE IF NOT EXISTS waitlist(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    for col in [("users","is_banned","INTEGER DEFAULT 0"),("users","credits","INTEGER DEFAULT 0"),("pricing_plans","monthly_credits","INTEGER DEFAULT 0"),("users","university","TEXT"),("users","department","TEXT"),("users","reset_code","TEXT")]:
        try:c.execute(f"ALTER TABLE {col[0]} ADD COLUMN {col[1]} {col[2]}")
        except:pass

    # v15: summaries_json kolonu ekle (ünite bazlı detaylı özetler)
    try:c.execute("ALTER TABLE documents ADD COLUMN summaries_json TEXT")
    except:pass

    # v15.2: Ünite bazlı özet ve test cache tabloları
    c.execute("""CREATE TABLE IF NOT EXISTS unit_summaries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id TEXT NOT NULL,
        unit_index INTEGER NOT NULL,
        unit_title TEXT,
        summary_json TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS unit_quizzes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id TEXT NOT NULL,
        unit_index INTEGER NOT NULL,
        unit_title TEXT,
        quiz_type TEXT DEFAULT 'quiz',
        quiz_json TEXT NOT NULL,
        num_questions INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    )""")

    if c.execute("SELECT COUNT(*) FROM credit_settings").fetchone()[0]==0:
        for k,v,ltr,len_ in [("summary_cost",5,"1 PDF Özet","1 PDF Summary"),("quiz_per_question",1,"1 Test Sorusu","1 Quiz Question"),("free_credits",40,"Ücretsiz Başlangıç Kredisi","Free Starting Credits")]:
            c.execute("INSERT INTO credit_settings(key,value,label_tr,label_en)VALUES(?,?,?,?)",(k,v,ltr,len_))
    if c.execute("SELECT COUNT(*) FROM credit_packages").fetchone()[0]==0:
        for name,credits,price,sort in [("Mini",200,29,1),("Orta",500,59,2),("Mega",1200,99,3)]:
            c.execute("INSERT INTO credit_packages(name,credits,price,sort_order)VALUES(?,?,?,?)",(name,credits,price,sort))
    if c.execute("SELECT COUNT(*) FROM pricing_plans").fetchone()[0]==0:
        plans=[("free","Ücretsiz",0,'["3 PDF analiz","Temel özet","5 soruluk test","Sınav takvimi"]','["3 PDF analysis","Basic summary","5-question test","Exam calendar"]',0,1,3,10,0),("starter","Starter",149,'["25 PDF/ay","Detaylı AI özet","10 soruluk test","Sınav takvimi","İstatistikler","Aylık 300 kredi"]','["25 PDF/mo","Detailed AI summary","10-question test","Exam calendar","Statistics","300 credits/mo"]',1,2,25,30,300),("pro","Pro",249,'["100 PDF/ay","Gelişmiş AI özet","20 soruluk test","Sınav takvimi","Detaylı istatistik","Öncelikli destek","Aylık 800 kredi"]','["100 PDF/mo","Advanced AI summary","20-question test","Exam calendar","Detailed stats","Priority support","800 credits/mo"]',0,3,100,50,800)]
        c.executemany("INSERT INTO pricing_plans(id,name,price,features_tr,features_en,popular,sort_order,pdf_limit,max_file_mb,monthly_credits)VALUES(?,?,?,?,?,?,?,?,?,?)",plans)
    conn.commit();conn.close()

@asynccontextmanager
async def lifespan(app:FastAPI): init_db();os.makedirs("uploads",exist_ok=True);yield

app=FastAPI(title="StudyMind AI",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class UserCreate(BaseModel): id:str;email:str;display_name:Optional[str]=None;password:Optional[str]=None;ref_code:Optional[str]=None;university:Optional[str]=None;department:Optional[str]=None
class UserLogin(BaseModel): email:str;password:str
class UserPlanUpdate(BaseModel): plan:str
class ProfileUpdate(BaseModel): display_name:Optional[str]=None; current_password:Optional[str]=None; new_password:Optional[str]=None; university:Optional[str]=None; department:Optional[str]=None
class AdminLogin(BaseModel): email:str;password:str
class PaymentRequest(BaseModel): user_id:str;plan:str;card_holder_name:str;card_number:str;expire_month:str;expire_year:str;cvc:str
class CreditPackageCreate(BaseModel): name:str;credits:int;price:float;sort_order:Optional[int]=0;active:Optional[int]=1
class CreditPackageUpdate(BaseModel): name:Optional[str]=None;credits:Optional[int]=None;price:Optional[float]=None;sort_order:Optional[int]=None;active:Optional[int]=None
class CreditSettingCreate(BaseModel): key:str;value:int;label_tr:str;label_en:str
class CreditSettingUpdate(BaseModel): key:str;value:int
class PricingPlanUpdate(BaseModel): price:Optional[float]=None;monthly_credits:Optional[int]=None
class BlogPostCreate(BaseModel): title_tr:str;title_en:Optional[str]="";excerpt_tr:Optional[str]="";excerpt_en:Optional[str]="";content_tr:str;content_en:Optional[str]="";category_tr:Optional[str]="";category_en:Optional[str]="";image:Optional[str]="📝";published:Optional[int]=1
class ExamCreate(BaseModel): title:str;exam_type:str;exam_date_start:str;exam_date_end:Optional[str]=None;description:Optional[str]=None
class NotificationCreate(BaseModel): title:str;message:str;type:Optional[str]="info"
class SiteSettingUpdate(BaseModel): key:str;value:str
class SupportMessage(BaseModel): message:str;user_email:Optional[str]="";user_name:Optional[str]=""
class AdminReply(BaseModel): reply:str
class ForgotPasswordReq(BaseModel): email:str
class ResetPasswordReq(BaseModel): email:str; code:str; new_password:str

def verify_admin(token): return token==hashlib.sha256(f"{ADMIN_EMAILS[0]}:{ADMIN_PASSWORD}:studymind".encode()).hexdigest()
def gen_invite_code(uid):return f"SM-{uid[-6:].upper()}"

@app.post("/api/users")
async def create_user(user:UserCreate,db=Depends(get_db)):
    safe_email = user.email.lower().strip()
    if db.execute("SELECT id FROM users WHERE LOWER(email)=?",(safe_email,)).fetchone(): raise HTTPException(400,"Kayıtlı email")
    pw=hash_pw(user.password) if user.password else None; code=gen_invite_code(user.id)
    db.execute("INSERT INTO users(id,email,display_name,password_hash,invite_code,credits,university,department)VALUES(?,?,?,?,?,40,?,?)",(user.id,safe_email,user.display_name,pw,code,user.university,user.department))
    db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",(user.id,40,"signup","Kayıt hediyesi"))
    db.commit(); return {"message":"Created","user_id":user.id,"invite_code":code}

@app.post("/api/users/login")
async def login_user(data:UserLogin,db=Depends(get_db)):
    safe_email = data.email.lower().strip()
    u=db.execute("SELECT * FROM users WHERE LOWER(email)=?",(safe_email,)).fetchone()
    if not u or (u["password_hash"] and u["password_hash"]!=hash_pw(data.password)):raise HTTPException(401,"Hatalı giriş")
    return {"user_id":u["id"],"email":u["email"],"display_name":u["display_name"],"plan":u["plan"],"credits":int(u["credits"] or 0)}

@app.get("/api/users/{uid}")
async def get_user(uid:str,db=Depends(get_db)):
    safe_uid = uid.lower().strip()
    u=db.execute("SELECT * FROM users WHERE id=? OR LOWER(email)=?",(uid,safe_uid)).fetchone()
    if not u:raise HTTPException(404)
    d=dict(u); d["credits"] = int(d["credits"] or 0); return d

def do_update_profile(uid, data, db):
    safe_uid = uid.lower().strip()
    u = db.execute("SELECT * FROM users WHERE id=? OR LOWER(email)=?", (uid, safe_uid)).fetchone()
    if not u: raise HTTPException(404, "Kullanıcı bulunamadı")
    
    updates = []; params = []
    if data.display_name is not None: updates.append("display_name=?"); params.append(data.display_name)
    if data.university is not None: updates.append("university=?"); params.append(data.university)
    if data.department is not None: updates.append("department=?"); params.append(data.department)
    if data.new_password:
        if u["password_hash"] and hash_pw(data.current_password) != u["password_hash"]:
            raise HTTPException(400, "Mevcut şifreniz yanlış")
        updates.append("password_hash=?"); params.append(hash_pw(data.new_password))
        
    if updates:
        query = f"UPDATE users SET {','.join(updates)} WHERE id=?"
        params.append(u["id"])
        db.execute(query, params)
        db.commit()
    return {"message": "Profil başarıyla güncellendi"}

@app.patch("/api/users/{uid}/profile")
@app.put("/api/users/{uid}/profile")
@app.post("/api/users/{uid}/profile")
async def update_profile_suffix(uid:str, data:ProfileUpdate, db=Depends(get_db)):
    return do_update_profile(uid, data, db)

@app.patch("/api/users/{uid}")
@app.put("/api/users/{uid}")
@app.post("/api/users/{uid}")
async def update_profile_direct(uid:str, data:ProfileUpdate, db=Depends(get_db)):
    return do_update_profile(uid, data, db)

# --- 🚀 PREMIUM HTML ŞİFRE SIFIRLAMA GÖNDERİMİ 🚀 ---
@app.post("/api/auth/forgot-password")
async def forgot_password(req: ForgotPasswordReq, db=Depends(get_db)):
    safe_email = req.email.lower().strip()
    u = db.execute("SELECT id, display_name FROM users WHERE LOWER(email)=?", (safe_email,)).fetchone()
    
    if not u:
        return {"message": "Sıfırlama kodu e-posta adresinize gönderildi."}
    
    user_name = u["display_name"] if u["display_name"] else "StudyMind Üyesi"
    code = ''.join(random.choices(string.digits, k=6))
    db.execute("UPDATE users SET reset_code=? WHERE LOWER(email)=?", (code, safe_email))
    db.commit()
    
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"StudyMind AI <{SMTP_EMAIL}>"
        msg['To'] = safe_email
        msg['Subject'] = "StudyMind - Şifre Sıfırlama Kodunuz"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <body style="margin: 0; padding: 0; background-color: #0b1120; font-family: Arial, sans-serif; color: #e2e8f0;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #0b1120; padding: 40px 0;">
                <tr>
                    <td align="center">
                        <table width="600" cellpadding="0" cellspacing="0" style="background-color: #0f172a; border-radius: 12px; padding: 40px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);">
                            <tr>
                                <td align="center" style="padding-bottom: 30px;">
                                    <h1 style="color: #38bdf8; margin: 0; font-size: 32px; letter-spacing: 1px;">StudyMind AI</h1>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding-bottom: 20px; color: #cbd5e1; font-size: 16px;">
                                    Merhaba <strong style="color: #ffffff;">{user_name}</strong>,<br><br>
                                    Şifre sıfırlama kodunuz:
                                </td>
                            </tr>
                            <tr>
                                <td align="center" style="padding-bottom: 30px;">
                                    <div style="background-color: #1e293b; border-radius: 8px; padding: 20px 40px; display: inline-block;">
                                        <span style="color: #38bdf8; font-size: 38px; font-weight: bold; letter-spacing: 12px;">{code}</span>
                                    </div>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding-bottom: 30px; color: #94a3b8; font-size: 14px; border-bottom: 1px solid #334155;">
                                    Bu kodu kimseyle paylaşmayın. Kod 10 dakika geçerlidir.
                                </td>
                            </tr>
                            <tr>
                                <td align="center" style="padding-top: 20px; color: #64748b; font-size: 12px; line-height: 1.5;">
                                    Bu e-postayı siz talep etmediyseniz, lütfen dikkate almayın.<br>
                                    © 2026 StudyMind AI
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Başarılı: PREMIUM HTML E-posta {safe_email} adresine gönderildi.")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SMTP HATASI: E-posta gönderilemedi! Lütfen main.py dosyasındaki SMTP_PASSWORD ayarlarınızı kontrol edin. Hata detayı: {e}")
        print(f"ACIL DURUM KODU: {code}")
    
    return {"message": "Sıfırlama kodu e-posta adresinize gönderildi."}

@app.post("/api/auth/reset-password")
async def reset_password(req: ResetPasswordReq, db=Depends(get_db)):
    safe_email = req.email.lower().strip()
    u = db.execute("SELECT * FROM users WHERE LOWER(email)=?", (safe_email,)).fetchone()
    
    if not u or u["reset_code"] != req.code:
        raise HTTPException(400, "Geçersiz veya süresi dolmuş kod.")
    
    new_hash = hash_pw(req.new_password)
    db.execute("UPDATE users SET password_hash=?, reset_code=NULL WHERE LOWER(email)=?", (new_hash, safe_email))
    db.commit()
    return {"message": "Şifreniz başarıyla güncellendi."}

@app.post("/api/admin/login")
async def admin_login(data:AdminLogin):
    if data.email not in ADMIN_EMAILS or hash_pw(data.password)!=ADMIN_HASH:raise HTTPException(403)
    return {"token":hashlib.sha256(f"{data.email}:{ADMIN_PASSWORD}:studymind".encode()).hexdigest(),"email":data.email}

@app.get("/api/admin/users")
async def admin_users(token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return [dict(u) for u in db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()]

@app.patch("/api/admin/users/{uid}/plan")
async def admin_plan(uid:str,data:UserPlanUpdate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    plan_id = data.plan.lower().strip(); db.execute("UPDATE users SET plan=?,plan_expires=NULL WHERE id=?",(plan_id,uid))
    added_credits = 0; p = db.execute("SELECT monthly_credits FROM pricing_plans WHERE LOWER(id)=?", (plan_id,)).fetchone()
    if p and p["monthly_credits"]: added_credits = int(p["monthly_credits"])
    elif plan_id == "starter": added_credits = 300
    elif plan_id == "pro": added_credits = 500
    if added_credits > 0:
        db.execute("UPDATE users SET credits=COALESCE(credits,0)+? WHERE id=?", (added_credits, uid))
        db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",(uid, added_credits, "admin_plan_change", f"{plan_id.upper()} plan kredisi"))
    db.commit(); return {"message":"Updated"}

@app.delete("/api/admin/users/{uid}")
async def admin_del_user(uid:str,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("DELETE FROM users WHERE id=?",(uid,));db.commit();return {"message":"Deleted"}

@app.patch("/api/admin/users/{uid}/ban")
async def admin_ban(uid:str,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    u = db.execute("SELECT is_banned FROM users WHERE id=?", (uid,)).fetchone()
    if not u: raise HTTPException(404)
    new_val = 0 if u["is_banned"] else 1
    db.execute("UPDATE users SET is_banned=? WHERE id=?", (new_val, uid)); db.commit()
    return {"message": "Banned" if new_val else "Unbanned"}

## ============================================================
## GERÇEK AI FONKSİYONLARI (Claude API) — v15 İYİLEŞTİRİLMİŞ
## ============================================================

def extract_pdf_text_full(path):
    """PDF'den TÜM metni çıkar (PyMuPDF)"""
    doc = fitz.open(path)
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text.strip()

def extract_pdf_text_pages(path, start_page, end_page):
    """PDF'den belirli sayfa aralığının metnini çıkar"""
    doc = fitz.open(path)
    text = ""
    for i in range(max(0, start_page-1), min(end_page, doc.page_count)):
        text += doc[i].get_text() + "\n"
    doc.close()
    return text.strip()

def call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=4096):
    """Claude API'ye istek gönder"""
    if not CLAUDE_API_KEY:
        raise Exception("Claude API anahtarı yapılandırılmamış. .env dosyasına CLAUDE_API_KEY ekleyin.")
    
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=120.0
    )
    
    if resp.status_code != 200:
        error_detail = resp.text[:300]
        raise Exception(f"Claude API hatası ({resp.status_code}): {error_detail}")
    
    data = resp.json()
    return data["content"][0]["text"]

def parse_json_response(raw):
    """AI yanıtından JSON çıkar (markdown code block desteği)"""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)

def ai_detect_units(text):
    """AI ile PDF'deki ünite/bölüm yapısını tespit et — v15.3 geliştirilmiş"""
    try:
        # İçindekiler sayfasını bulmaya çalış
        toc_text = ""
        text_lower = text.lower()
        for keyword in ["içindekiler", "i̇çindekiler", "İçindekiler", "İÇİNDEKİLER", "table of contents"]:
            pos = text_lower.find(keyword.lower())
            if pos >= 0:
                toc_text = text[pos:pos+8000]
                break
        
        # İçindekiler bulunamazsa BÖLÜM/Ünite kelimelerini ara
        if not toc_text:
            for keyword in ["BÖLÜM 1", "Bölüm 1", "Ünite 1", "ÜNİTE 1", "1."]:
                pos = text_lower.find(keyword.lower())
                if pos >= 0:
                    toc_text = text[max(0, pos-200):pos+8000]
                    break
        
        # Hiçbiri bulunamazsa ilk 10000 karakteri kullan
        sample_text = toc_text if toc_text else text[:10000]

        prompt = f"""Bu bir üniversite ders kitabı veya ders notunun metnidir. İçindeki tüm ana bölüm/ünite başlıklarını tespit et.

ÖNEMLİ KURALLAR:
1. Sadece ANA bölüm/ünite başlıklarını bul (alt başlıkları, giriş, özet gibi kısımları ATLA)
2. Başlıkları TAM İSİMLERİYLE yaz — "BÖLÜM 2" değil, o bölümün gerçek konu adını bul
3. Farklı kitap formatlarını tanı:
   - "BÖLÜM 1 Ekip Çalışması" → başlık: "Ekip Çalışması"
   - "Ünite 3: Karar Verme" → başlık: "Karar Verme"
   - "1. Bilim Felsefesi ve Bilimsel Araştırma" → başlık: "Bilim Felsefesi ve Bilimsel Araştırma"
   - Bazen bölüm numarası bir satırda, başlık sonraki satırlarda olur:
     "BÖLÜM 6" (satır 1)
     "Ekip Farklılıklarını" (satır 2)
     "Yönetme" (satır 3)
     → başlık: "Ekip Farklılıklarını Yönetme"
   - Bazen başlık bölüm numarasından önce gelir:
     "Ekip Çatışmalarını" (satır 1)
     "BÖLÜM 7 Yönetme ve" (satır 2)
     "Uzlaşmayı Sağlama" (satır 3)
     → başlık: "Ekip Çatışmalarını Yönetme ve Uzlaşmayı Sağlama"
4. TÜM bölümleri bul — 8, 10, 14 bölüm olabilir, hepsini listele
5. Sayfa numaralarını (3, 25, 55, 107 gibi rakamları) ATLA
6. Her bölüme 1'den başlayarak numara ver
7. Türkçe yanıt ver

YANITINI MUTLAKA şu JSON formatında ver, başka hiçbir şey yazma:
{{
  "units": [
    {{"number": 1, "title": "Bölümün gerçek konu başlığı"}},
    {{"number": 2, "title": "Bölümün gerçek konu başlığı"}}
  ]
}}

METİN:
{sample_text}"""
        
        raw = call_claude(prompt, max_tokens=2048)
        result = parse_json_response(raw)
        return result.get("units", [])
    except Exception as e:
        print(f"[AI HATA] Ünite tespiti başarısız: {e}")
        return []


## ============================================================
## v15 İYİLEŞTİRİLMİŞ: DETAYLI ÖZET ÜRETİMİ
## ============================================================

def ai_generate_summary(text, unit_title="", unit_number=0):
    """Claude ile DETAYLI özet üret — en az 300 kelime, alt başlıklı, anahtar kavramlı"""
    try:
        title_instruction = ""
        if unit_title and unit_number:
            title_instruction = f'Başlık MUTLAKA şu formatta olsun: "Ünite {unit_number} — {unit_title} Özeti"'
        else:
            title_instruction = 'Başlık konuyu açıkça yansıtsın.'

        prompt = f"""Sen bir üniversite ders asistanısın. Aşağıdaki ders notunu analiz ederek
kapsamlı ve öğretici bir özet hazırla.

KURALLAR:
1. {title_instruction}
2. Özet EN AZ 300 kelime, EN FAZLA 500 kelime olmalı
3. En az 3, en fazla 5 alt başlık kullan (numaralı: 1. 2. 3. ...)
4. Her alt başlık altında en az 3 madde olmalı
5. Maddeler açıklayıcı ve öğretici olmalı — tek kelime değil, tam cümle (en az 15 kelime)
6. Sınav perspektifinden önemli noktaları vurgula
7. Sonunda 5-10 adet "Anahtar Kavram" listele

YANITINI MUTLAKA şu JSON formatında ver, başka hiçbir şey yazma:
{{
  "title": "Özet başlığı",
  "sections": [
    {{
      "heading": "1. Alt Başlık Adı",
      "bullets": [
        "Açıklayıcı madde 1 (en az 15 kelime olacak şekilde detaylı yaz)",
        "Açıklayıcı madde 2",
        "Açıklayıcı madde 3"
      ]
    }}
  ],
  "keyConcepts": ["Kavram 1", "Kavram 2", "Kavram 3", "Kavram 4", "Kavram 5"],
  "wordCount": 350
}}

DERS NOTU:
{text[:12000]}"""

        raw = call_claude(prompt, max_tokens=4096)
        result = parse_json_response(raw)

        # Eski formatla uyumluluk: summary string + key_points list
        summary_text = result.get("title", "Özet") + "\n\n"
        for section in result.get("sections", []):
            summary_text += section.get("heading", "") + "\n"
            for bullet in section.get("bullets", []):
                summary_text += f"• {bullet}\n"
            summary_text += "\n"

        key_points = result.get("keyConcepts", [])

        return summary_text.strip(), key_points, result

    except json.JSONDecodeError:
        return raw[:2000], ["AI yanıtı ayrıştırılamadı — ham özet kullanıldı"], None
    except Exception as e:
        print(f"[AI HATA] Özet üretimi başarısız: {e}")
        return f"AI özet üretimi sırasında hata oluştu: {str(e)[:200]}", ["Hata oluştu"], None


## ============================================================
## v15 İYİLEŞTİRİLMİŞ: BATCH TEST ÜRETİMİ (tıkanmayan)
## ============================================================

def ai_generate_quiz(text, num_questions=10, batch_index=0, exclude_questions=None):
    """Claude ile çoktan seçmeli test üret — batch destekli, tekrar engelleyici, JSON kurtarmalı"""
    try:
        # Token limitine takılmamak için metin ve soru sayısını sınırla
        max_text = min(len(text), 6000)  # 12000 yerine 6000 — token tasarrufu
        num_questions = min(num_questions, 10)  # Batch başına max 10

        exclude_instruction = ""
        if exclude_questions and len(exclude_questions) > 0:
            recent = exclude_questions[-3:]  # Son 3 soru yeterli
            exclude_instruction = (
                "\nDİKKAT: Bu sorulardan FARKLI sorular üret:\n"
                + "\n".join(f"- {q[:80]}" for q in recent)
                + "\n"
            )

        prompt = f"""Sen bir üniversite sınav sorusu hazırlama uzmanısın. {num_questions} adet çoktan seçmeli soru hazırla.

KURALLAR:
- 4 şık (A, B, C, D), 1 doğru cevap
- Soru tipleri çeşitli olsun
- Açıklamalar KISA ama öğretici olsun (1-2 cümle)
- Türkçe yaz
- Bu {batch_index + 1}. grup
{exclude_instruction}
YANITINI MUTLAKA şu JSON formatında ver, başka hiçbir şey yazma:
[
  {{
    "question": "Soru metni?",
    "options": ["A şıkkı", "B şıkkı", "C şıkkı", "D şıkkı"],
    "correct_answer": "Doğru şıkkın tam metni",
    "explanation": "Kısa açıklama"
  }}
]

DERS NOTU:
{text[:max_text]}"""
        
        raw = call_claude(prompt, max_tokens=4096)
        
        # JSON parse — yarım kalan yanıtı kurtarma
        try:
            questions = parse_json_response(raw)
        except json.JSONDecodeError:
            # Yanıt yarım kalmış, kurtarmaya çalış
            print(f"[AI UYARI] JSON yarım kaldı, kurtarma deneniyor...")
            raw_clean = raw.strip()
            if raw_clean.startswith("```"):
                parts = raw_clean.split("```")
                if len(parts) >= 2:
                    raw_clean = parts[1]
                    if raw_clean.startswith("json"):
                        raw_clean = raw_clean[4:]
            raw_clean = raw_clean.strip()
            
            # Son tamamlanmış objeye kadar kes
            last_complete = raw_clean.rfind("}")
            if last_complete > 0:
                truncated = raw_clean[:last_complete + 1]
                # Sonuna ] ekle
                if not truncated.rstrip().endswith("]"):
                    truncated = truncated.rstrip().rstrip(",") + "\n]"
                try:
                    questions = json.loads(truncated)
                    print(f"[AI KURTARMA] {len(questions)} soru kurtarıldı")
                except json.JSONDecodeError:
                    print(f"[AI HATA] JSON kurtarma da başarısız")
                    return []
            else:
                return []
        
        valid = []
        for q in questions:
            if isinstance(q, dict) and all(k in q for k in ["question","options","correct_answer"]):
                if "explanation" not in q:
                    q["explanation"] = "Açıklama mevcut değil."
                if isinstance(q["options"], list) and len(q["options"]) == 4:
                    valid.append(q)
        return valid[:num_questions]
    except Exception as e:
        print(f"[AI HATA] Test üretimi başarısız: {e}")
        return []


## ============================================================
## v15 YENİ: ÇIKMIŞ SINAV FORMATINDA SORU ÜRETİMİ
## ============================================================

def ai_generate_past_exam(text, unit_title="", num_questions=10, batch_index=0, exclude_questions=None):
    """Sınav formatına uygun sorular üretir — batch destekli, JSON kurtarmalı"""
    try:
        max_text = min(len(text), 6000)
        num_questions = min(num_questions, 10)  # Batch başına max 10

        exclude_instruction = ""
        if exclude_questions and len(exclude_questions) > 0:
            recent = exclude_questions[-3:]
            exclude_instruction = (
                "\nDİKKAT: Bu sorulardan FARKLI sorular üret:\n"
                + "\n".join(f"- {q[:80]}" for q in recent)
                + "\n"
            )

        prompt = f"""Sen bir üniversite sınav sorusu uzmanısın. {num_questions} soru üret.

SINAV FORMATI:
- "Aşağıdakilerden hangisi..." formatı
- "I, II, III — Yukarıdakilerden hangileri..." formatı
- Akademik dil, 4 şık, 1 doğru cevap
- Her soruya sınav dönemi etiketi: (2024 Final), (2024 Vize), (2023 Final) vb.
- Açıklamalar KISA (1-2 cümle)
- Türkçe yaz
- Bu {batch_index + 1}. grup
{exclude_instruction}

YANITINI MUTLAKA şu JSON formatında ver, başka hiçbir şey yazma:
[
  {{
    "question": "(2024 Final) Soru metni?",
    "options": ["A şıkkı", "B şıkkı", "C şıkkı", "D şıkkı"],
    "correct_answer": "Doğru şıkkın tam metni",
    "explanation": "Kısa açıklama",
    "examPeriod": "2024 Final"
  }}
]

ÜNİTE: {unit_title}
DERS NOTU:
{text[:max_text]}"""
        
        raw = call_claude(prompt, max_tokens=4096)
        
        try:
            questions = parse_json_response(raw)
        except json.JSONDecodeError:
            print(f"[AI UYARI] Çıkmış soru JSON yarım kaldı, kurtarma deneniyor...")
            raw_clean = raw.strip()
            if raw_clean.startswith("```"):
                parts = raw_clean.split("```")
                if len(parts) >= 2:
                    raw_clean = parts[1]
                    if raw_clean.startswith("json"):
                        raw_clean = raw_clean[4:]
            raw_clean = raw_clean.strip()
            
            last_complete = raw_clean.rfind("}")
            if last_complete > 0:
                truncated = raw_clean[:last_complete + 1]
                if not truncated.rstrip().endswith("]"):
                    truncated = truncated.rstrip().rstrip(",") + "\n]"
                try:
                    questions = json.loads(truncated)
                    print(f"[AI KURTARMA] {len(questions)} çıkmış soru kurtarıldı")
                except json.JSONDecodeError:
                    return []
            else:
                return []
        
        valid = []
        for q in questions:
            if isinstance(q, dict) and all(k in q for k in ["question","options","correct_answer"]):
                if "explanation" not in q:
                    q["explanation"] = "Açıklama mevcut değil."
                if "examPeriod" not in q:
                    q["examPeriod"] = "2024 Final"
                if isinstance(q["options"], list) and len(q["options"]) == 4:
                    valid.append(q)
        return valid[:num_questions]
    except Exception as e:
        print(f"[AI HATA] Çıkmış soru üretimi başarısız: {e}")
        return []


def find_unit_text(full_text, unit_title, all_units, unit_index):
    """Tam metinden belirli bir ünitenin metnini çıkar — v15.1: İçindekiler atlama düzeltmesi"""
    text_lower = full_text.lower()
    title_lower = unit_title.lower().strip()
    
    # Tüm eşleşmeleri bul (içindekiler + gerçek ünite)
    matches = []
    search_start = 0
    while True:
        pos = text_lower.find(title_lower, search_start)
        if pos == -1:
            break
        matches.append(pos)
        search_start = pos + 1
    
    # Kısa arama (başlık tam bulunamazsa)
    if not matches:
        words = title_lower.split()[:3]
        search = " ".join(words)
        search_start = 0
        while True:
            pos = text_lower.find(search, search_start)
            if pos == -1:
                break
            matches.append(pos)
            search_start = pos + 1
    
    if not matches:
        return ""
    
    # İçindekiler bölümünü atla: Son eşleşmeyi kullan
    # İçindekiler genellikle metnin başında, gerçek içerik sonlarda
    if len(matches) > 1:
        start = matches[-1]
    else:
        start = matches[0]
    
    # Sonraki ünitenin başlangıcını bul
    end = len(full_text)
    if unit_index + 1 < len(all_units):
        next_title = all_units[unit_index + 1]["title"].lower().strip()
        
        # Sonraki ünite için de tüm eşleşmeleri bul
        next_matches = []
        ns = 0
        while True:
            np = text_lower.find(next_title, ns)
            if np == -1:
                # Kısa arama
                next_words = next_title.split()[:3]
                next_search = " ".join(next_words)
                np2 = text_lower.find(next_search, ns)
                if np2 == -1:
                    break
                next_matches.append(np2)
                ns = np2 + 1
            else:
                next_matches.append(np)
                ns = np + 1
        
        if next_matches:
            # Start'tan sonraki ilk eşleşmeyi bitiş noktası olarak al
            valid_ends = [m for m in next_matches if m > start]
            if valid_ends:
                end = valid_ends[0]
    
    result = full_text[start:end].strip()
    
    # Çok kısa ise içindekiler satırı yakalanmış olabilir, diğer eşleşmeleri dene
    if len(result) < 200 and len(matches) > 1:
        for m in matches:
            test_end = len(full_text)
            if unit_index + 1 < len(all_units):
                next_title_l = all_units[unit_index + 1]["title"].lower().strip()
                npos = text_lower.find(next_title_l, m + len(title_lower))
                if npos > m:
                    test_end = npos
            candidate = full_text[m:test_end].strip()
            if len(candidate) > 200:
                return candidate
    
    return result if result else ""

def get_credit_cost(db, key, default=1):
    """credit_settings tablosundan maliyet oku"""
    r = db.execute("SELECT value FROM credit_settings WHERE key=?", (key,)).fetchone()
    return int(r["value"]) if r else default

## --- UPLOAD ENDPOINT (PDF kaydet + ünite yapısı çıkar) ---

@app.post("/api/documents/upload")
async def upload_doc(file:UploadFile=File(...), user_id:str=Form("guest"), db=Depends(get_db)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Sadece PDF dosyası yüklenebilir")
    
    # PDF kaydet
    did = str(uuid.uuid4())
    path = f"uploads/{did}.pdf"
    with open(path, "wb") as f:
        f.write(await file.read())
    
    # PDF'den tüm metni çıkar
    pdf_text = extract_pdf_text_full(path)
    if not pdf_text or len(pdf_text) < 50:
        os.remove(path)
        raise HTTPException(400, "PDF'den yeterli metin çıkarılamadı. Lütfen metin tabanlı bir PDF yükleyin.")
    
    # AI ile ünite yapısını tespit et
    units = ai_detect_units(pdf_text)
    units_json = json.dumps(units, ensure_ascii=False)
    
    # v15.3: PDF dosyasını diskten SİL — orijinal eser saklanmaz, sadece çıkarılan metin işlenir
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"[GÜVENLİK] PDF dosyası silindi: {path}")
    except Exception as e:
        print(f"[UYARI] PDF silinemedi: {e}")
    
    # DB'ye kaydet — özet ve quiz boş (ünite seçimine göre üretilecek)
    db.execute("INSERT INTO documents(id,user_id,filename,original_text,summary,key_points,quiz_data,status)VALUES(?,?,?,?,?,?,?,?)",
               (did, user_id, file.filename, pdf_text[:60000], "", units_json, "[]", "uploaded"))
    db.commit()
    
    return {
        "document_id": did,
        "filename": file.filename,
        "status": "uploaded",
        "units": units,
        "credits_used": 0
    }

## ============================================================
## v15.2: ÜNİTE BAZLI ÖZET ÜRETME (CACHE DESTEKLİ)
## ============================================================

@app.post("/api/documents/{did}/generate-summary")
async def generate_summary(did:str, user_id:str=Form("guest"), selected_units:str=Form("all"), db=Depends(get_db)):
    doc = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    if not doc:
        raise HTTPException(404, "Doküman bulunamadı")
    
    pdf_text = doc["original_text"] or ""
    if len(pdf_text) < 50:
        raise HTTPException(400, "Bu doküman için yeterli metin mevcut değil")
    
    summary_cost = get_credit_cost(db, "summary_cost", 5)
    safe_user_id = user_id.lower().strip()
    all_units = json.loads(doc["key_points"]) if doc["key_points"] else []
    
    if selected_units == "all" or not all_units:
        # Tüm doküman özeti — eski davranış
        u = db.execute("SELECT credits FROM users WHERE id=? OR LOWER(email)=?", (user_id, safe_user_id)).fetchone()
        current_credits = int(u["credits"]) if u and u["credits"] is not None else 0
        if user_id != "guest" and current_credits < summary_cost:
            raise HTTPException(400, f"Yetersiz kredi! Özet için {summary_cost} kredi gerekiyor. Mevcut: {current_credits}")
        
        target_text = pdf_text[:15000]
        summary, key_points, structured = ai_generate_summary(target_text)
        db.execute("UPDATE documents SET summary=?, status='analyzed' WHERE id=?", (summary, did))
        if user_id != "guest":
            db.execute("UPDATE users SET credits = COALESCE(credits,0) - ? WHERE id=? OR LOWER(email)=?", (summary_cost, user_id, safe_user_id))
            db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",
                       (user_id, -summary_cost, "usage", f"Özet ({doc['filename']}) — {summary_cost} kredi"))
        db.commit()
        return {"summary": summary, "key_points": key_points, "structured_summaries": None, "credits_used": summary_cost}
    
    # Ünite bazlı özet — cache kontrollü
    selected_indices = [int(x) for x in selected_units.split(",")]
    chunks = []
    all_structured = {}
    new_units = []  # Henüz özeti olmayan üniteler
    cached_units = []  # Zaten özeti olan üniteler
    
    for idx in selected_indices:
        if 0 <= idx < len(all_units):
            existing = db.execute("SELECT summary_json FROM unit_summaries WHERE document_id=? AND unit_index=? ORDER BY created_at DESC LIMIT 1", (did, idx)).fetchone()
            if existing:
                cached_units.append(idx)
                cached_data = json.loads(existing["summary_json"])
                all_structured[str(idx)] = cached_data
                summary_text = cached_data.get("title", "Özet") + "\n\n"
                for section in cached_data.get("sections", []):
                    summary_text += section.get("heading", "") + "\n"
                    for bullet in section.get("bullets", []):
                        summary_text += f"• {bullet}\n"
                    summary_text += "\n"
                chunks.append(summary_text.strip())
            else:
                new_units.append(idx)
    
    # Sadece yeni üniteler için kredi düş
    if new_units:
        u = db.execute("SELECT credits FROM users WHERE id=? OR LOWER(email)=?", (user_id, safe_user_id)).fetchone()
        current_credits = int(u["credits"]) if u and u["credits"] is not None else 0
        if user_id != "guest" and current_credits < summary_cost:
            raise HTTPException(400, f"Yetersiz kredi! Özet için {summary_cost} kredi gerekiyor. Mevcut: {current_credits}")
        
        for idx in new_units:
            unit = all_units[idx]
            unit_text = find_unit_text(pdf_text, unit["title"], all_units, idx)
            if unit_text:
                u_summary, u_kp, u_structured = ai_generate_summary(
                    unit_text[:8000],
                    unit_title=unit.get("title", ""),
                    unit_number=unit.get("number", idx + 1)
                )
                chunks.append(u_summary)
                if u_structured:
                    all_structured[str(idx)] = u_structured
                    # Cache'e kaydet
                    db.execute("INSERT INTO unit_summaries(document_id,unit_index,unit_title,summary_json)VALUES(?,?,?,?)",
                               (did, idx, unit.get("title",""), json.dumps(u_structured, ensure_ascii=False)))
        
        # Kredi düş (sadece yeni üniteler için)
        if user_id != "guest":
            db.execute("UPDATE users SET credits = COALESCE(credits,0) - ? WHERE id=? OR LOWER(email)=?", (summary_cost, user_id, safe_user_id))
            db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",
                       (user_id, -summary_cost, "usage", f"Özet ({len(new_units)} yeni ünite) — {summary_cost} kredi"))
    
    summary = "\n\n---\n\n".join(chunks) if chunks else "Özet üretilemedi."
    key_points = []
    for s in all_structured.values():
        key_points.extend(s.get("keyConcepts", []))
    
    if all_structured:
        db.execute("UPDATE documents SET summaries_json=? WHERE id=?",
                   (json.dumps(all_structured, ensure_ascii=False), did))
    db.execute("UPDATE documents SET summary=?, status='analyzed' WHERE id=?", (summary, did))
    db.commit()
    
    return {
        "summary": summary,
        "key_points": key_points,
        "structured_summaries": all_structured,
        "credits_used": summary_cost if new_units else 0,
        "cached_units": cached_units,
        "new_units": new_units
    }


## ============================================================
## v15.2: ÜNİTE BAZLI TEST ÜRETME (CACHE + ESKİ TESTLERİ KORUMA)
## ============================================================

@app.post("/api/documents/{did}/generate-quiz")
async def generate_quiz(did:str, num_questions:int=Form(10), user_id:str=Form("guest"), selected_units:str=Form("all"), db=Depends(get_db)):
    doc = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    if not doc:
        raise HTTPException(404, "Doküman bulunamadı")
    
    pdf_text = doc["original_text"] or ""
    if len(pdf_text) < 50:
        raise HTTPException(400, "Bu doküman için yeterli metin mevcut değil")
    
    num_questions = max(1, min(num_questions, 100))
    per_question_cost = get_credit_cost(db, "quiz_per_question", 1)
    total_cost = num_questions * per_question_cost
    
    safe_user_id = user_id.lower().strip()
    u = db.execute("SELECT credits FROM users WHERE id=? OR LOWER(email)=?", (user_id, safe_user_id)).fetchone()
    current_credits = int(u["credits"]) if u and u["credits"] is not None else 0
    
    if user_id != "guest" and current_credits < total_cost:
        raise HTTPException(400, f"Yetersiz kredi! {num_questions} soru için {total_cost} kredi gerekiyor. Mevcut: {current_credits}")
    
    all_units = json.loads(doc["key_points"]) if doc["key_points"] else []
    
    if selected_units == "all" or not all_units:
        target_text = pdf_text[:15000]
        all_quiz = []
        remaining = num_questions
        batch_idx = 0
        while remaining > 0:
            batch_size = min(10, remaining)
            exclude = [q["question"] for q in all_quiz]
            batch = ai_generate_quiz(target_text, batch_size, batch_index=batch_idx, exclude_questions=exclude)
            if not batch:
                break
            all_quiz.extend(batch)
            remaining -= len(batch)
            batch_idx += 1
        quiz = all_quiz
    else:
        selected_indices = [int(x) for x in selected_units.split(",")]
        quiz = []
        questions_per_unit = max(1, num_questions // len(selected_indices))
        extra = num_questions - (questions_per_unit * len(selected_indices))
        
        for i, idx in enumerate(selected_indices):
            if 0 <= idx < len(all_units):
                unit = all_units[idx]
                unit_text = find_unit_text(pdf_text, unit["title"], all_units, idx)
                if unit_text:
                    unit_q_count = questions_per_unit + (1 if i < extra else 0)
                    remaining = unit_q_count
                    batch_idx = 0
                    while remaining > 0:
                        batch_size = min(10, remaining)
                        exclude = [q["question"] for q in quiz]
                        batch = ai_generate_quiz(
                            unit_text[:8000], batch_size,
                            batch_index=batch_idx,
                            exclude_questions=exclude
                        )
                        if not batch:
                            break
                        for q in batch:
                            q["unitTitle"] = unit.get("title", f"Ünite {idx+1}")
                        quiz.extend(batch)
                        remaining -= len(batch)
                        batch_idx += 1
    
    if not quiz:
        raise HTTPException(500, "Test üretimi başarısız oldu. Lütfen tekrar deneyin.")
    
    actual_count = len(quiz)
    actual_cost = actual_count * per_question_cost
    
    # v15.2: Eski quiz_data'yı EZMEYİP, unit_quizzes tablosuna kaydet
    quiz_json = json.dumps(quiz, ensure_ascii=False)
    
    # Her ünite için ayrı ayrı cache'e kaydet
    if selected_units != "all" and all_units:
        selected_indices = [int(x) for x in selected_units.split(",")]
        for idx in selected_indices:
            unit = all_units[idx] if idx < len(all_units) else {}
            unit_questions = [q for q in quiz if q.get("unitTitle","") == unit.get("title","")]
            if unit_questions:
                db.execute("INSERT INTO unit_quizzes(document_id,unit_index,unit_title,quiz_type,quiz_json,num_questions)VALUES(?,?,?,?,?,?)",
                           (did, idx, unit.get("title",""), "quiz", json.dumps(unit_questions, ensure_ascii=False), len(unit_questions)))
    else:
        db.execute("INSERT INTO unit_quizzes(document_id,unit_index,unit_title,quiz_type,quiz_json,num_questions)VALUES(?,?,?,?,?,?)",
                   (did, -1, "Tüm Konular", "quiz", quiz_json, actual_count))
    
    # Eski quiz_data'yı da güncelle (mevcut frontend uyumluluğu)
    db.execute("UPDATE documents SET quiz_data=? WHERE id=?", (quiz_json, did))
    
    if user_id != "guest":
        db.execute("UPDATE users SET credits = COALESCE(credits,0) - ? WHERE id=? OR LOWER(email)=?", (actual_cost, user_id, safe_user_id))
        db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",
                   (user_id, -actual_cost, "usage", f"Test ({actual_count} soru) — {actual_cost} kredi"))
    
    db.commit()
    return {
        "quiz": quiz,
        "count": actual_count,
        "credits_used": actual_cost
    }


## ============================================================
## v15.2: ÇIKMIŞ SINAV SORULARI (CACHE DESTEKLİ)
## ============================================================

@app.post("/api/documents/{did}/generate-past-exam")
async def generate_past_exam(did:str, num_questions:int=Form(10), user_id:str=Form("guest"), selected_units:str=Form("all"), db=Depends(get_db)):
    """Sınav formatında soru üretir"""
    doc = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    if not doc:
        raise HTTPException(404, "Doküman bulunamadı")
    
    pdf_text = doc["original_text"] or ""
    if len(pdf_text) < 50:
        raise HTTPException(400, "Bu doküman için yeterli metin mevcut değil")
    
    num_questions = max(1, min(num_questions, 100))
    per_question_cost = get_credit_cost(db, "quiz_per_question", 1)
    total_cost = num_questions * per_question_cost
    
    safe_user_id = user_id.lower().strip()
    u = db.execute("SELECT credits FROM users WHERE id=? OR LOWER(email)=?", (user_id, safe_user_id)).fetchone()
    current_credits = int(u["credits"]) if u and u["credits"] is not None else 0
    
    if user_id != "guest" and current_credits < total_cost:
        raise HTTPException(400, f"Yetersiz kredi! {num_questions} soru için {total_cost} kredi gerekiyor. Mevcut: {current_credits}")
    
    all_units = json.loads(doc["key_points"]) if doc["key_points"] else []
    all_questions = []
    
    if selected_units == "all" or not all_units:
        target_text = pdf_text[:15000]
        remaining = num_questions
        batch_idx = 0
        while remaining > 0:
            batch_size = min(10, remaining)
            exclude = [q["question"] for q in all_questions]
            batch = ai_generate_past_exam(target_text, "Tüm Konular", batch_size, batch_index=batch_idx, exclude_questions=exclude)
            if not batch:
                break
            all_questions.extend(batch)
            remaining -= len(batch)
            batch_idx += 1
    else:
        selected_indices = [int(x) for x in selected_units.split(",")]
        questions_per_unit = max(1, num_questions // len(selected_indices))
        extra = num_questions - (questions_per_unit * len(selected_indices))
        
        for i, idx in enumerate(selected_indices):
            if 0 <= idx < len(all_units):
                unit = all_units[idx]
                unit_text = find_unit_text(pdf_text, unit["title"], all_units, idx)
                if unit_text:
                    unit_q_count = questions_per_unit + (1 if i < extra else 0)
                    remaining = unit_q_count
                    batch_idx = 0
                    while remaining > 0:
                        batch_size = min(10, remaining)
                        exclude = [q["question"] for q in all_questions]
                        batch = ai_generate_past_exam(
                            unit_text[:8000], unit.get("title", ""),
                            batch_size, batch_index=batch_idx,
                            exclude_questions=exclude
                        )
                        if not batch:
                            break
                        for q in batch:
                            q["unitTitle"] = unit.get("title", f"Ünite {idx+1}")
                        all_questions.extend(batch)
                        remaining -= len(batch)
                        batch_idx += 1
    
    if not all_questions:
        raise HTTPException(500, "Çıkmış soru üretimi başarısız oldu. Lütfen tekrar deneyin.")
    
    actual_count = len(all_questions)
    actual_cost = actual_count * per_question_cost
    
    # v15.2: unit_quizzes tablosuna kaydet (quiz_type='past_exam')
    quiz_json = json.dumps(all_questions, ensure_ascii=False)
    if selected_units != "all" and all_units:
        selected_indices = [int(x) for x in selected_units.split(",")]
        for idx in selected_indices:
            unit = all_units[idx] if idx < len(all_units) else {}
            unit_questions = [q for q in all_questions if q.get("unitTitle","") == unit.get("title","")]
            if unit_questions:
                db.execute("INSERT INTO unit_quizzes(document_id,unit_index,unit_title,quiz_type,quiz_json,num_questions)VALUES(?,?,?,?,?,?)",
                           (did, idx, unit.get("title",""), "past_exam", json.dumps(unit_questions, ensure_ascii=False), len(unit_questions)))
    else:
        db.execute("INSERT INTO unit_quizzes(document_id,unit_index,unit_title,quiz_type,quiz_json,num_questions)VALUES(?,?,?,?,?,?)",
                   (did, -1, "Tüm Konular", "past_exam", quiz_json, actual_count))
    
    if user_id != "guest":
        db.execute("UPDATE users SET credits = COALESCE(credits,0) - ? WHERE id=? OR LOWER(email)=?", (actual_cost, user_id, safe_user_id))
        db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",
                   (user_id, -actual_cost, "usage", f"Çıkmış sorular ({actual_count} soru) — {actual_cost} kredi"))
    
    db.commit()
    return {
        "quiz": all_questions,
        "count": actual_count,
        "credits_used": actual_cost
    }


## ============================================================
## v15 YENİ: PDF EXPORT ENDPOINT'İ
## ============================================================

@app.get("/api/documents/{did}/export-summary-pdf")
async def export_summary_pdf(did:str, db=Depends(get_db)):
    """Özetleri PDF olarak indir"""
    doc = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    if not doc:
        raise HTTPException(404, "Doküman bulunamadı")
    
    summary = doc["summary"] or "Henüz özet üretilmedi."
    filename = doc["filename"] or "Özet"
    
    # Basit HTML → PDF dönüşümü (WeasyPrint yoksa düz metin PDF)
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; padding: 40px; color: #333; line-height: 1.8; }}
            h1 {{ color: #0ea5e9; border-bottom: 2px solid #0ea5e9; padding-bottom: 8px; font-size: 22px; }}
            h2 {{ color: #1e3a5f; margin-top: 24px; font-size: 16px; }}
            ul {{ line-height: 1.8; margin-bottom: 16px; }}
            li {{ margin-bottom: 4px; }}
            .footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #ddd; color: #999; font-size: 11px; text-align: center; }}
        </style>
    </head>
    <body>
        <h1>📘 {filename} — Özet</h1>
        <div>{summary.replace(chr(10), '<br>')}</div>
        <div class="footer">StudyMind AI tarafından üretilmiştir — {datetime.now().strftime('%d.%m.%Y')}</div>
    </body>
    </html>
    """
    
    # WeasyPrint varsa PDF üret, yoksa HTML olarak döndür
    try:
        from weasyprint import HTML as WeasyprintHTML
        pdf_bytes = WeasyprintHTML(string=html_content).write_pdf()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=StudyMind-Ozet-{did[:8]}.pdf"}
        )
    except ImportError:
        # WeasyPrint yüklü değilse HTML olarak indir
        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename=StudyMind-Ozet-{did[:8]}.html"}
        )


@app.delete("/api/documents/{did}")
async def delete_document(did:str, db=Depends(get_db)):
    try:
        if os.path.exists(f"uploads/{did}.pdf"): os.remove(f"uploads/{did}.pdf")
    except: pass
    # v15.2: İlişkili cache verilerini de sil
    db.execute("DELETE FROM unit_summaries WHERE document_id=?", (did,))
    db.execute("DELETE FROM unit_quizzes WHERE document_id=?", (did,))
    db.execute("DELETE FROM documents WHERE id=?", (did,)); db.commit(); return {"message": "Silindi"}


## ============================================================
## v15.2 YENİ: ÜNİTE GEÇMİŞ VE SİLME ENDPOINT'LERİ
## ============================================================

@app.get("/api/documents/{did}/unit-summaries")
async def get_unit_summaries(did:str, db=Depends(get_db)):
    """Dokümanın tüm ünite özetlerini geçmişiyle birlikte döndür"""
    rows = db.execute("SELECT id, unit_index, unit_title, summary_json, created_at FROM unit_summaries WHERE document_id=? ORDER BY unit_index, created_at DESC", (did,)).fetchall()
    result = {}
    for r in rows:
        idx = str(r["unit_index"])
        if idx not in result:
            result[idx] = {
                "unit_title": r["unit_title"],
                "current": json.loads(r["summary_json"]),
                "history": []
            }
        result[idx]["history"].append({
            "id": r["id"],
            "created_at": r["created_at"],
        })
    return result

@app.get("/api/documents/{did}/unit-quizzes")
async def get_unit_quizzes(did:str, quiz_type:str="", db=Depends(get_db)):
    """Dokümanın tüm ünite testlerini geçmişiyle birlikte döndür"""
    if quiz_type:
        rows = db.execute("SELECT id, unit_index, unit_title, quiz_type, num_questions, created_at FROM unit_quizzes WHERE document_id=? AND quiz_type=? ORDER BY created_at DESC", (did, quiz_type)).fetchall()
    else:
        rows = db.execute("SELECT id, unit_index, unit_title, quiz_type, num_questions, created_at FROM unit_quizzes WHERE document_id=? ORDER BY created_at DESC", (did,)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/documents/{did}/unit-quiz/{quiz_id}")
async def get_unit_quiz_detail(did:str, quiz_id:int, db=Depends(get_db)):
    """Belirli bir testin sorularını döndür"""
    row = db.execute("SELECT * FROM unit_quizzes WHERE id=? AND document_id=?", (quiz_id, did)).fetchone()
    if not row:
        raise HTTPException(404, "Test bulunamadı")
    return {
        "id": row["id"],
        "unit_index": row["unit_index"],
        "unit_title": row["unit_title"],
        "quiz_type": row["quiz_type"],
        "quiz": json.loads(row["quiz_json"]),
        "num_questions": row["num_questions"],
        "created_at": row["created_at"]
    }

@app.delete("/api/documents/{did}/unit-summary/{summary_id}")
async def delete_unit_summary(did:str, summary_id:int, db=Depends(get_db)):
    """Belirli bir ünite özetini sil"""
    db.execute("DELETE FROM unit_summaries WHERE id=? AND document_id=?", (summary_id, did))
    db.commit()
    return {"message": "Özet silindi"}

@app.delete("/api/documents/{did}/unit-quiz/{quiz_id}")
async def delete_unit_quiz(did:str, quiz_id:int, db=Depends(get_db)):
    """Belirli bir testi sil"""
    db.execute("DELETE FROM unit_quizzes WHERE id=? AND document_id=?", (quiz_id, did))
    db.commit()
    return {"message": "Test silindi"}

@app.get("/api/documents/user/{uid}")
async def get_user_docs(uid:str,db=Depends(get_db)):return [dict(d) for d in db.execute("SELECT * FROM documents WHERE user_id=? OR user_id='guest' ORDER BY created_at DESC",(uid,)).fetchall()]

@app.get("/api/documents/{did}")
async def get_doc(did:str,db=Depends(get_db)):
    d=db.execute("SELECT * FROM documents WHERE id=?",(did,)).fetchone()
    if not d:raise HTTPException(404)
    r=dict(d)
    # key_points alanı: ünite listesi (units) VEYA anahtar noktalar olabilir
    raw_kp = r.get("key_points", "")
    parsed_kp = json.loads(raw_kp) if raw_kp else []
    
    # Eğer parsed_kp içinde dict var ise (units formatı), units olarak ayarla
    if parsed_kp and isinstance(parsed_kp[0], dict) and "title" in parsed_kp[0]:
        r["units"] = parsed_kp
        r["key_points"] = []
    else:
        r["units"] = []
        r["key_points"] = parsed_kp
    
    r["quiz_data"] = json.loads(r["quiz_data"]) if r.get("quiz_data") else []
    
    # v15: Yapılandırılmış özetleri de döndür
    r["structured_summaries"] = json.loads(r.get("summaries_json") or "{}") if r.get("summaries_json") else {}
    
    # v15.2: Cache'deki ünite özetlerini de döndür
    cached_summaries = db.execute(
        "SELECT unit_index, summary_json FROM unit_summaries WHERE document_id=? ORDER BY unit_index, created_at DESC", (did,)
    ).fetchall()
    if cached_summaries:
        cs = {}
        for row in cached_summaries:
            idx = str(row["unit_index"])
            if idx not in cs:  # Her ünitenin en son özeti
                cs[idx] = json.loads(row["summary_json"])
        if cs:
            r["structured_summaries"] = cs
    
    # v15.2: Test geçmişini döndür
    quiz_history_items = db.execute(
        "SELECT id, unit_index, unit_title, quiz_type, num_questions, created_at FROM unit_quizzes WHERE document_id=? ORDER BY created_at DESC", (did,)
    ).fetchall()
    r["quiz_history_list"] = [dict(q) for q in quiz_history_items]
    
    # v15.2: Hangi ünitelerin özeti var bilgisi
    summary_unit_indices = db.execute(
        "SELECT DISTINCT unit_index FROM unit_summaries WHERE document_id=?", (did,)
    ).fetchall()
    r["summarized_units"] = [row["unit_index"] for row in summary_unit_indices]
    
    return r

@app.post("/api/documents/{did}/quiz-result")
async def submit_quiz_result(did:str, data:dict, db=Depends(get_db)):
    user_id = data.get("user_id", "guest")
    answers = data.get("answers", {})
    
    doc = db.execute("SELECT quiz_data FROM documents WHERE id=?", (did,)).fetchone()
    if not doc: raise HTTPException(404)
    
    quiz = json.loads(doc["quiz_data"]) if doc["quiz_data"] else []
    if not quiz: raise HTTPException(400, "Test verisi bulunamadı")
    
    correct = 0
    results_list = []
    for i, q in enumerate(quiz):
        user_answer = answers.get(str(i), answers.get(i, None))
        is_correct = user_answer == q.get("correct_answer", "")
        if is_correct: correct += 1
        results_list.append({
            **q,
            "user_answer": user_answer,
            "is_correct": is_correct
        })
    
    total = len(quiz)
    percentage = round((correct / total) * 100) if total > 0 else 0
    
    # Kaydet
    db.execute("INSERT INTO quiz_history(user_id,document_id,score,total,percentage,answers_json)VALUES(?,?,?,?,?,?)",
               (user_id, did, correct, total, percentage, json.dumps(answers, ensure_ascii=False)))
    db.execute("INSERT INTO study_sessions(user_id,document_id,duration_minutes,quiz_score,quiz_total)VALUES(?,?,?,?,?)",
               (user_id, did, 0, correct, total))
    db.commit()
    
    return {
        "score": correct,
        "total": total,
        "percentage": percentage,
        "results": results_list
    }

@app.post("/api/users/{uid}/streak")
async def update_streak(uid:str, db=Depends(get_db)):
    u = db.execute("SELECT streak, last_study_date FROM users WHERE id=?", (uid,)).fetchone()
    if not u: raise HTTPException(404)
    
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    last = u["last_study_date"]
    streak = int(u["streak"] or 0)
    
    if last == today:
        return {"streak": streak, "message": "Bugün zaten güncellendi"}
    elif last == yesterday:
        streak += 1
    else:
        streak = 1
    
    db.execute("UPDATE users SET streak=?, last_study_date=? WHERE id=?", (streak, today, uid))
    db.commit()
    return {"streak": streak, "message": "Streak güncellendi"}

@app.get("/api/pricing")
async def get_pricing(db=Depends(get_db)):
    plans=db.execute("SELECT * FROM pricing_plans ORDER BY sort_order").fetchall();result=[]
    for p in plans:
        d=dict(p);d["features_tr"]=json.loads(d["features_tr"]) if d["features_tr"] else [];d["features_en"]=json.loads(d["features_en"]) if d["features_en"] else [];result.append(d)
    return result
@app.put("/api/admin/pricing/{pid}")
async def admin_update_pricing(pid:str,data:PricingPlanUpdate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    updates={k:v for k,v in data.dict().items() if v is not None}
    if updates: db.execute(f"UPDATE pricing_plans SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",list(updates.values())+[pid]); db.commit()
    return {"message":"Updated"}

@app.get("/api/credits/packages")
async def get_credit_packages(db=Depends(get_db)): return [dict(r) for r in db.execute("SELECT * FROM credit_packages WHERE active=1 ORDER BY sort_order").fetchall()]
@app.get("/api/admin/credits/packages")
async def admin_get_credit_packages(token:str="",db=Depends(get_db)): return [dict(r) for r in db.execute("SELECT * FROM credit_packages ORDER BY sort_order").fetchall()]
@app.post("/api/admin/credits/packages")
async def admin_create_package(data:CreditPackageCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT INTO credit_packages(name,credits,price,sort_order,active)VALUES(?,?,?,?,?)",(data.name,data.credits,data.price,data.sort_order,data.active)); db.commit(); return {"message":"Created"}
@app.put("/api/admin/credits/packages/{pid}")
async def admin_update_package(pid:int,data:CreditPackageUpdate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    updates={k:v for k,v in data.dict().items() if v is not None}
    if updates: db.execute(f"UPDATE credit_packages SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",list(updates.values())+[pid]); db.commit()
    return {"message":"Updated"}
@app.delete("/api/admin/credits/packages/{pid}")
async def admin_delete_package(pid:int,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("DELETE FROM credit_packages WHERE id=?",(pid,)); db.commit(); return {"message":"Deleted"}

@app.get("/api/credits/settings")
async def get_credit_settings(db=Depends(get_db)): return {r["key"]:r["value"] for r in db.execute("SELECT * FROM credit_settings").fetchall()}
@app.get("/api/admin/credits/settings")
async def admin_credit_settings(token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return [dict(r) for r in db.execute("SELECT * FROM credit_settings").fetchall()]
@app.post("/api/admin/credits/settings")
async def admin_create_credit_setting(data:CreditSettingCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT OR REPLACE INTO credit_settings(key,value,label_tr,label_en)VALUES(?,?,?,?)",(data.key,data.value,data.label_tr,data.label_en)); db.commit(); return {"message":"Created"}
@app.put("/api/admin/credits/settings")
async def admin_update_credit_setting(data:CreditSettingUpdate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("UPDATE credit_settings SET value=? WHERE key=?",(data.value,data.key)); db.commit(); return {"message":"Updated"}
@app.delete("/api/admin/credits/settings/{key}")
async def admin_del_credit_setting(key:str,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("DELETE FROM credit_settings WHERE key=?",(key,)); db.commit(); return {"message":"Deleted"}
@app.post("/api/admin/credits/give/{uid}")
async def admin_give_credits(uid:str,amount:int=0,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    safe_uid = uid.lower().strip()
    db.execute("UPDATE users SET credits=COALESCE(credits,0)+? WHERE id=? OR LOWER(email)=?",(amount,uid,safe_uid)); db.commit(); return {"message":"Ok"}

@app.post("/api/payments/create")
async def create_payment(payment:PaymentRequest,db=Depends(get_db)):
    plan_str = payment.plan.strip(); plan_id_lower = plan_str.lower()
    plan = db.execute("SELECT price, monthly_credits FROM pricing_plans WHERE LOWER(id)=?", (plan_id_lower,)).fetchone()
    if plan:
        amount = plan["price"]; pid = str(uuid.uuid4())
        db.execute("INSERT INTO payments(id,user_id,amount,status,plan)VALUES(?,?,?,?,?)",(pid,payment.user_id,amount,"success",plan_id_lower))
        db.execute("UPDATE users SET plan=?,plan_expires=NULL WHERE id=?",(plan_id_lower,payment.user_id))
        added_credits = int(plan["monthly_credits"]) if plan["monthly_credits"] else 0
        if plan_id_lower == "starter" and added_credits == 0: added_credits = 300
        elif plan_id_lower == "pro" and added_credits == 0: added_credits = 500
        if added_credits > 0:
            db.execute("UPDATE users SET credits=COALESCE(credits,0)+? WHERE id=?",(added_credits, payment.user_id))
            db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",(payment.user_id, added_credits, "plan_purchase", f"{plan_str.upper()} aboneliği"))
        db.commit(); return {"payment_id":pid,"status":"success"}
    pkg = db.execute("SELECT id, name, price, credits FROM credit_packages WHERE id=? OR LOWER(name)=?", (plan_str, plan_id_lower)).fetchone()
    if pkg:
        amount = pkg["price"]; added_credits = pkg["credits"]; pid = str(uuid.uuid4())
        db.execute("INSERT INTO payments(id,user_id,amount,status,plan)VALUES(?,?,?,?,?)",(pid,payment.user_id,amount,"success",f"credit_pkg_{pkg['id']}"))
        db.execute("UPDATE users SET credits=COALESCE(credits,0)+? WHERE id=?",(added_credits, payment.user_id))
        db.execute("INSERT INTO credit_transactions(user_id,amount,type,description)VALUES(?,?,?,?)",(payment.user_id, added_credits, "credit_purchase", f"{pkg['name']} paketi"))
        db.commit(); return {"payment_id":pid,"status":"success"}
    raise HTTPException(400, "Geçersiz plan/paket.")

@app.post("/api/support/{uid}/send")
async def send_support(uid:str,data:SupportMessage,db=Depends(get_db)):
    u=db.execute("SELECT email,display_name FROM users WHERE id=?",(uid,)).fetchone()
    email=u["email"] if u else data.user_email; name=u["display_name"] if u else data.user_name
    db.execute("INSERT INTO support_tickets(user_id,user_email,user_name,message)VALUES(?,?,?,?)",(uid,email,name,data.message))
    db.commit(); return {"message":"Gönderildi"}
@app.get("/api/support/{uid}/live")
async def get_live_chat(uid:str,db=Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT * FROM support_tickets WHERE user_id=? ORDER BY created_at ASC",(uid,)).fetchall()]
@app.get("/api/support/{uid}/history")
async def get_support_history(uid:str,db=Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT * FROM support_tickets WHERE user_id=? ORDER BY created_at DESC",(uid,)).fetchall()]
@app.get("/api/admin/support/users")
async def admin_support_users(token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return [dict(r) for r in db.execute("SELECT DISTINCT user_id,user_email,user_name,(SELECT COUNT(*) FROM support_tickets st2 WHERE st2.user_id=st.user_id AND st2.status='open') as open_count,(SELECT MAX(created_at) FROM support_tickets st3 WHERE st3.user_id=st.user_id) as last_msg FROM support_tickets st WHERE user_id IS NOT NULL ORDER BY last_msg DESC").fetchall()]
@app.get("/api/admin/support/chat/{uid}")
async def admin_get_chat(uid:str,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return [dict(r) for r in db.execute("SELECT * FROM support_tickets WHERE user_id=? ORDER BY created_at ASC",(uid,)).fetchall()]
@app.post("/api/admin/support/chat/{uid}/reply")
async def admin_chat_reply(uid:str,data:AdminReply,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT INTO support_tickets(user_id,user_email,user_name,message,admin_reply,status)VALUES(?,?,?,?,?,?)",(uid,"admin","Admin",data.reply,None,"admin_reply"))
    db.commit(); return {"message":"Replied"}

@app.get("/api/exams")
async def get_exams(db=Depends(get_db)):return [dict(e) for e in db.execute("SELECT * FROM exams ORDER BY exam_date_start").fetchall()]
@app.post("/api/admin/exams")
async def admin_create_exam(data:ExamCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT INTO exams(title,exam_type,exam_date_start,exam_date_end,description)VALUES(?,?,?,?,?)",(data.title,data.exam_type,data.exam_date_start,data.exam_date_end,data.description)); db.commit(); return {"message":"Created"}
@app.put("/api/admin/exams/{eid}")
async def admin_update_exam(eid:int,data:ExamCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("UPDATE exams SET title=?,exam_type=?,exam_date_start=?,exam_date_end=?,description=? WHERE id=?",(data.title,data.exam_type,data.exam_date_start,data.exam_date_end,data.description,eid)); db.commit(); return {"message":"Updated"}
@app.delete("/api/admin/exams/{eid}")
async def admin_delete_exam(eid:int,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("DELETE FROM exams WHERE id=?",(eid,)); db.commit(); return {"message":"Deleted"}

@app.get("/api/blog")
async def get_blog(db=Depends(get_db)):return [dict(b) for b in db.execute("SELECT * FROM blog_posts WHERE published=1 ORDER BY created_at DESC").fetchall()]
@app.get("/api/blog/{bid}")
async def get_blog_post(bid:int,db=Depends(get_db)):
    b=db.execute("SELECT * FROM blog_posts WHERE id=?",(bid,)).fetchone()
    if not b:raise HTTPException(404)
    return dict(b)
@app.post("/api/admin/blog")
async def admin_create_blog(data:BlogPostCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT INTO blog_posts(title_tr,title_en,excerpt_tr,excerpt_en,content_tr,content_en,category_tr,category_en,image,published)VALUES(?,?,?,?,?,?,?,?,?,?)",(data.title_tr,data.title_en,data.excerpt_tr,data.excerpt_en,data.content_tr,data.content_en,data.category_tr,data.category_en,data.image,data.published)); db.commit(); return {"message":"Created"}
@app.put("/api/admin/blog/{bid}")
async def admin_update_blog(bid:int,data:BlogPostCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("UPDATE blog_posts SET title_tr=?,title_en=?,excerpt_tr=?,excerpt_en=?,content_tr=?,content_en=?,category_tr=?,category_en=?,image=?,published=? WHERE id=?",(data.title_tr,data.title_en,data.excerpt_tr,data.excerpt_en,data.content_tr,data.content_en,data.category_tr,data.category_en,data.image,data.published,bid)); db.commit(); return {"message":"Updated"}
@app.delete("/api/admin/blog/{bid}")
async def admin_delete_blog(bid:int,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("DELETE FROM blog_posts WHERE id=?",(bid,)); db.commit(); return {"message":"Deleted"}
@app.get("/api/admin/blog")
async def admin_get_blogs(token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return [dict(b) for b in db.execute("SELECT * FROM blog_posts ORDER BY created_at DESC").fetchall()]

@app.get("/api/notifications")
async def get_notifications(db=Depends(get_db)):return [dict(n) for n in db.execute("SELECT * FROM notifications WHERE active=1 ORDER BY created_at DESC").fetchall()]
@app.post("/api/admin/notifications")
async def admin_create_notification(data:NotificationCreate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT INTO notifications(title,message,type)VALUES(?,?,?)",(data.title,data.message,data.type)); db.commit(); return {"message":"Created"}
@app.delete("/api/admin/notifications/{nid}")
async def admin_del_notification(nid:int,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("DELETE FROM notifications WHERE id=?",(nid,)); db.commit(); return {"message":"Deleted"}

@app.get("/api/admin/site-settings")
async def admin_get_settings(token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return {r["key"]:r["value"] for r in db.execute("SELECT * FROM site_settings").fetchall()}
@app.post("/api/admin/site-settings")
async def admin_update_settings(data:SiteSettingUpdate,token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    db.execute("INSERT OR REPLACE INTO site_settings(key,value)VALUES(?,?)",(data.key,data.value)); db.commit(); return {"message":"Updated"}

@app.get("/api/public/stats")
async def public_stats(db=Depends(get_db)):
    users = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    docs = db.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
    quizzes = db.execute("SELECT COUNT(*) as c FROM quiz_history").fetchone()["c"]
    return {"users": users, "documents": docs, "quizzes": quizzes}

@app.get("/api/invites/{uid}")
async def get_invites(uid:str,db=Depends(get_db)):
    u=db.execute("SELECT invite_code FROM users WHERE id=?",(uid,)).fetchone()
    invites=db.execute("SELECT i.*, u.display_name, u.email FROM invites i JOIN users u ON i.invitee_id=u.id WHERE i.inviter_id=? ORDER BY i.created_at DESC",(uid,)).fetchall()
    return {"invite_code":u["invite_code"] if u else "","invites":[dict(i) for i in invites]}
@app.get("/api/admin/invites")
async def admin_invites(token:str="",db=Depends(get_db)):
    if not verify_admin(token):raise HTTPException(403)
    return [dict(r) for r in db.execute("SELECT i.*, u1.display_name as inviter_name, u2.display_name as invitee_name FROM invites i LEFT JOIN users u1 ON i.inviter_id=u1.id LEFT JOIN users u2 ON i.invitee_id=u2.id ORDER BY i.created_at DESC").fetchall()]

@app.get("/api/quotes")
async def get_quotes():
    quotes_tr = [
        "Eğitim en güçlü silahınızdır. — Nelson Mandela",
        "Öğrenme asla zihni yormaz. — Leonardo da Vinci",
        "Yarının anahtarı bugünkü hazırlıktır. — Malcolm X",
        "Bilgi güçtür. — Francis Bacon",
        "Başarı, hazırlık ile fırsatın buluştuğu yerdir. — Bobby Unser",
        "Her düştüğümüzde ayağa kalkmak en büyük zaferdir. — Konfüçyüs",
        "Her gün bir adım ileri. — StudyMind AI",
        "Deha %1 ilham, %99 terdir. — Thomas Edison",
        "Eğitim geleceğin pasaportudur. — Malcolm X",
        "Okumak zihin için, egzersiz beden için neyse odur. — Joseph Addison",
        "Düşünmeden öğrenmek faydasızdır. — Konfüçyüs",
        "İyi başlamak, yarıyı tamamlamaktır. — Aristoteles",
        "Azim, başarının anahtarıdır. — Benjamin Franklin",
        "Öğrenmek bir hazinedir. — Anonim",
        "Bilgi edinmek her Müslümana farzdır. — Hz. Muhammed",
        "İnsan ancak çalışarak yükselir. — Voltaire",
        "Kendini geliştiren dünyayı geliştirir. — StudyMind AI",
        "Sabır acıdır, meyvesi tatlıdır. — Jean-Jacques Rousseau",
    ]
    quotes_en = [
        "Education is the most powerful weapon. — Nelson Mandela",
        "Learning never exhausts the mind. — Leonardo da Vinci",
        "The key to tomorrow is preparation today. — Malcolm X",
        "Knowledge is power. — Francis Bacon",
        "Success is where preparation meets opportunity. — Bobby Unser",
        "The greatest glory is in rising every time we fall. — Confucius",
        "Every day one step forward. — StudyMind AI",
        "Genius is 1% inspiration and 99% perspiration. — Thomas Edison",
        "Education is the passport to the future. — Malcolm X",
        "Reading is to the mind what exercise is to the body. — Joseph Addison",
        "Learning without thought is labor lost. — Confucius",
        "Well begun is half done. — Aristotle",
        "Perseverance is the key to success. — Benjamin Franklin",
        "An investment in knowledge pays the best interest. — Benjamin Franklin",
        "The only way to do great work is to love what you do. — Steve Jobs",
        "Tell me and I forget. Teach me and I remember. — Benjamin Franklin",
        "Self-improvement improves the world. — StudyMind AI",
        "Patience is bitter, but its fruit is sweet. — Jean-Jacques Rousseau",
        "It does not matter how slowly you go as long as you do not stop. — Confucius",
        "The beautiful thing about learning is nobody can take it away from you. — B.B. King",
    ]
    return {"tr": quotes_tr, "en": quotes_en}

@app.get("/api/plan-limits/{plan_id}")
async def get_plan_limits(plan_id:str, db=Depends(get_db)):
    p = db.execute("SELECT pdf_limit, max_file_mb FROM pricing_plans WHERE LOWER(id)=?", (plan_id.lower(),)).fetchone()
    if p:
        return {"pdf_limit": p["pdf_limit"], "max_file_mb": p["max_file_mb"]}
    return {"pdf_limit": 3, "max_file_mb": 10}

@app.get("/api/study-sessions/{uid}/stats")
async def get_stats(uid:str, db=Depends(get_db)):
    total_s = db.execute("SELECT COUNT(*) as c FROM study_sessions WHERE user_id=?", (uid,)).fetchone()["c"]
    total_m = db.execute("SELECT COALESCE(SUM(duration_minutes),0) as m FROM study_sessions WHERE user_id=?", (uid,)).fetchone()["m"]
    avg_q = db.execute("SELECT COALESCE(AVG(CASE WHEN quiz_total>0 THEN quiz_score*100.0/quiz_total END),0) as a FROM study_sessions WHERE user_id=? AND quiz_total>0", (uid,)).fetchone()["a"]
    total_q = db.execute("SELECT COUNT(*) as c FROM quiz_history WHERE user_id=?", (uid,)).fetchone()["c"]
    weekly = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_min = db.execute("SELECT COALESCE(SUM(duration_minutes),0) as m FROM study_sessions WHERE user_id=? AND DATE(session_date)=?", (uid,d)).fetchone()["m"]
        weekly.append({"date": d, "minutes": day_min})
    return {"total_sessions": total_s, "total_minutes": total_m, "average_quiz_score": round(avg_q,1), "total_quizzes": total_q, "weekly": weekly}

@app.get("/api/messages/{uid}/unread")
async def get_unread(uid:str, db=Depends(get_db)):
    r = db.execute("SELECT COUNT(*) as c FROM messages WHERE receiver_id=? AND is_read=0", (uid,)).fetchone()
    return {"count": r["c"] if r else 0}

@app.get("/api/messages/{uid}")
async def get_messages(uid:str, db=Depends(get_db)):
    sent = db.execute("SELECT m.*, u.display_name as receiver_name FROM messages m LEFT JOIN users u ON m.receiver_id=u.id WHERE m.sender_id=? ORDER BY m.created_at DESC", (uid,)).fetchall()
    received = db.execute("SELECT m.*, u.display_name as sender_name FROM messages m LEFT JOIN users u ON m.sender_id=u.id WHERE m.receiver_id=? ORDER BY m.created_at DESC", (uid,)).fetchall()
    return {"sent": [dict(r) for r in sent], "received": [dict(r) for r in received]}

@app.post("/api/messages/{uid}/send")
async def send_message(uid:str, data:dict, db=Depends(get_db)):
    receiver_id = data.get("receiver_id","")
    content = data.get("content","")
    if not receiver_id or not content: raise HTTPException(400, "receiver_id ve content gerekli")
    db.execute("INSERT INTO messages(sender_id,receiver_id,content)VALUES(?,?,?)", (uid, receiver_id, content))
    db.commit()
    return {"message": "Sent"}

@app.post("/api/messages/{mid}/read")
async def mark_read(mid:int, db=Depends(get_db)):
    db.execute("UPDATE messages SET is_read=1 WHERE id=?", (mid,))
    db.commit()
    return {"message": "Read"}

@app.get("/api/leaderboard")
async def get_leaderboard(db=Depends(get_db)):
    rows = db.execute("""
        SELECT u.id, u.display_name, u.streak, u.plan,
               COUNT(DISTINCT q.id) as quiz_count,
               COALESCE(AVG(q.percentage),0) as avg_score,
               COUNT(DISTINCT d.id) as doc_count
        FROM users u
        LEFT JOIN quiz_history q ON u.id = q.user_id
        LEFT JOIN documents d ON u.id = d.user_id
        WHERE u.is_banned = 0
        GROUP BY u.id
        ORDER BY quiz_count DESC, avg_score DESC
        LIMIT 50
    """).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/quiz-history/{uid}")
async def get_history(uid:str, db=Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT q.*, d.filename FROM quiz_history q JOIN documents d ON q.document_id=d.id WHERE q.user_id=? ORDER BY q.created_at DESC", (uid,)).fetchall()]

@app.get("/api/quiz-history/{uid}/{did}")
async def get_doc_quiz_history(uid:str, did:str, db=Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT * FROM quiz_history WHERE user_id=? AND document_id=? ORDER BY created_at DESC", (uid, did)).fetchall()]


## ============================================================
## v15.3: TELİF HAKKI BİLDİRİM SİSTEMİ (DMCA / Uyar-Kaldır)
## ============================================================

class CopyrightReport(BaseModel):
    reporter_name: str
    reporter_email: str
    reporter_role: Optional[str] = ""
    document_id: Optional[str] = ""
    document_filename: Optional[str] = ""
    description: str

@app.post("/api/copyright/report")
async def submit_copyright_report(data: CopyrightReport, db=Depends(get_db)):
    """Telif hakkı ihlal bildirimi al — uyar-kaldır mekanizması"""
    if not data.reporter_name or not data.reporter_email or not data.description:
        raise HTTPException(400, "Ad, e-posta ve açıklama zorunludur")
    
    db.execute(
        "INSERT INTO copyright_reports(reporter_name,reporter_email,reporter_role,document_id,document_filename,description)VALUES(?,?,?,?,?,?)",
        (data.reporter_name, data.reporter_email, data.reporter_role, data.document_id, data.document_filename, data.description)
    )
    db.commit()
    
    # Admin'e e-posta gönder (best effort)
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"StudyMind AI <{SMTP_EMAIL}>"
        msg['To'] = ADMIN_EMAILS[0]
        msg['Subject'] = f"⚠️ Telif Hakkı Bildirimi — {data.reporter_name}"
        html_body = f"""
        <html><body style="font-family:Arial;padding:20px;background:#0b1120;color:#e2e8f0;">
        <h2 style="color:#f59e0b;">⚠️ Yeni Telif Hakkı Bildirimi</h2>
        <p><b>Bildiren:</b> {data.reporter_name} ({data.reporter_email})</p>
        <p><b>Rol:</b> {data.reporter_role or 'Belirtilmedi'}</p>
        <p><b>Doküman ID:</b> {data.document_id or 'Belirtilmedi'}</p>
        <p><b>Dosya:</b> {data.document_filename or 'Belirtilmedi'}</p>
        <p><b>Açıklama:</b><br>{data.description}</p>
        <hr style="border-color:#334155;">
        <p style="color:#94a3b8;font-size:12px;">StudyMind AI Uyar-Kaldır Sistemi — {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
        </body></html>
        """
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"[SMTP] Telif bildirimi e-postası gönderilemedi: {e}")
    
    return {"message": "Bildiriminiz alındı. En kısa sürede değerlendirilecektir."}

@app.get("/api/admin/copyright-reports")
async def get_copyright_reports(token:str="", db=Depends(get_db)):
    """Admin: Tüm telif bildirimlerini listele"""
    if not verify_admin(token): raise HTTPException(403)
    rows = db.execute("SELECT * FROM copyright_reports ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/copyright-reports/{rid}/action")
async def copyright_report_action(rid:int, data:dict, db=Depends(get_db)):
    """Admin: Telif bildirimine aksiyon al (onay/ret/doküman sil)"""
    token = data.get("token","")
    if not verify_admin(token): raise HTTPException(403)
    
    action = data.get("action","")  # "resolve", "reject", "delete_document"
    notes = data.get("notes","")
    
    report = db.execute("SELECT * FROM copyright_reports WHERE id=?", (rid,)).fetchone()
    if not report: raise HTTPException(404)
    
    if action == "delete_document" and report["document_id"]:
        # İlgili dokümanı ve tüm verilerini sil
        did = report["document_id"]
        try:
            if os.path.exists(f"uploads/{did}.pdf"): os.remove(f"uploads/{did}.pdf")
        except: pass
        db.execute("DELETE FROM unit_summaries WHERE document_id=?", (did,))
        db.execute("DELETE FROM unit_quizzes WHERE document_id=?", (did,))
        db.execute("DELETE FROM documents WHERE id=?", (did,))
    
    db.execute("UPDATE copyright_reports SET status=?, admin_notes=? WHERE id=?",
               (action, notes, rid))
    db.commit()
    return {"message": f"Bildirim '{action}' olarak işlendi."}


## ============================================================
## v15.3: ORİJİNAL METİN TEMİZLEME (Telif Koruma Katmanı)
## ============================================================

@app.post("/api/documents/{did}/cleanup-text")
async def cleanup_original_text(did:str, db=Depends(get_db)):
    """Özet ve test üretildikten sonra original_text'i kısalt — telif koruma"""
    doc = db.execute("SELECT status, summary FROM documents WHERE id=?", (did,)).fetchone()
    if not doc: raise HTTPException(404)
    
    # Sadece analiz edilmiş dokümanlar için
    if doc["status"] == "analyzed" and doc["summary"]:
        # Orijinal metni kısalt: sadece ilk 500 karakter bırak (ünite tespiti için)
        db.execute("UPDATE documents SET original_text = SUBSTR(original_text, 1, 500) WHERE id=?", (did,))
        db.commit()
        return {"message": "Orijinal metin kısaltıldı", "cleaned": True}
    
    return {"message": "Doküman henüz analiz edilmemiş, temizlik yapılmadı", "cleaned": False}


## ============================================================
## v15.3: KULLANIM KOŞULLARI & GİZLİLİK (Statik İçerik)
## ============================================================

@app.get("/api/legal/terms")
async def get_terms():
    """Kullanım koşulları içeriğini döndür"""
    return {
        "title_tr": "Kullanım Koşulları",
        "title_en": "Terms of Service",
        "last_updated": "2026-03-25",
        "sections_tr": [
            {
                "heading": "1. Hizmet Tanımı",
                "content": "StudyMind AI, kullanıcıların yükledikleri ders materyallerinden yapay zeka destekli özet, test ve çalışma materyali üreten bir eğitim teknolojisi platformudur. Platform, içerik barındırma (hosting) hizmeti sunmaktadır."
            },
            {
                "heading": "2. Kullanıcı Sorumlulukları",
                "content": "Kullanıcı, platforma yüklediği tüm dosyaların telif hakkı sorumluluğunun kendisine ait olduğunu kabul eder. Kullanıcı, yalnızca kullanım hakkına sahip olduğu veya telif hakkı sahibinin izin verdiği materyalleri yükleyebilir. Üçüncü şahısların fikri mülkiyet haklarını ihlal eden içeriklerin yüklenmesi kesinlikle yasaktır."
            },
            {
                "heading": "3. Fikri Mülkiyet ve Telif Hakları",
                "content": "StudyMind AI, kullanıcılar tarafından yüklenen içeriklerin telif hakkı durumunu kontrol etme yükümlülüğü taşımaz. Platform, 5651 sayılı İnternet Kanunu kapsamında 'yer sağlayıcı' (hosting provider) statüsündedir. Telif hakkı ihlali bildirimlerinde 'Uyar-Kaldır' mekanizması uygulanır ve bildirim üzerine ilgili içerik derhal kaldırılır."
            },
            {
                "heading": "4. İçerik İşleme ve Veri Saklama",
                "content": "Yüklenen PDF dosyaları yapay zeka tarafından analiz edilerek özet ve test üretimi yapılır. Üretilen özet ve test içerikleri özgün eserlerdir ve orijinal materyalin birebir kopyası değildir. Orijinal dosya metni, analiz tamamlandıktan sonra sunucularımızdan kısaltılır veya silinir. Platform, orijinal eserlerin dağıtımını veya paylaşımını yapmaz."
            },
            {
                "heading": "5. Uyar-Kaldır Mekanizması",
                "content": "Telif hakkı sahipleri veya yetkili temsilcileri, platforma yüklenen içeriklerin haklarını ihlal ettiğini düşünmeleri halinde 'Telif Hakkı Bildirimi' formu aracılığıyla bildirimde bulunabilir. Bildirim üzerine ilgili içerik en geç 24 saat içinde kaldırılır. Tekrarlayan ihlallerde kullanıcı hesabı askıya alınabilir."
            },
            {
                "heading": "6. Kredi Sistemi ve Ödemeler",
                "content": "Platform, AI özet ve test üretim hizmeti karşılığında kredi sistemi kullanmaktadır. Satılan kredi, AI işlem kapasitesidir; telif hakkı koruması altındaki içeriklerin satışı değildir. Ödeme yapıldıktan sonra kullanılan krediler iade edilmez."
            },
            {
                "heading": "7. Sorumluluk Sınırlandırması",
                "content": "StudyMind AI, kullanıcılar tarafından yüklenen içeriklerden kaynaklanan telif hakkı ihlallerinden sorumlu tutulamaz. Platform, üçüncü şahısların haklarını ihlal eden içeriklere karşı 'Uyar-Kaldır' mekanizması ile en kısa sürede müdahale eder. AI tarafından üretilen özet ve testlerin doğruluğu garanti edilmez."
            },
            {
                "heading": "8. Değişiklikler",
                "content": "StudyMind AI, bu kullanım koşullarını önceden bildirim yaparak değiştirme hakkını saklı tutar. Güncel koşullar her zaman bu sayfada yayınlanır."
            }
        ],
        "sections_en": [
            {
                "heading": "1. Service Description",
                "content": "StudyMind AI is an educational technology platform that generates AI-powered summaries, quizzes, and study materials from user-uploaded course materials. The platform operates as a hosting service provider."
            },
            {
                "heading": "2. User Responsibilities",
                "content": "Users acknowledge that they are solely responsible for the copyright status of all files uploaded to the platform. Users may only upload materials they have the right to use or materials for which the copyright holder has granted permission. Uploading content that infringes on third-party intellectual property rights is strictly prohibited."
            },
            {
                "heading": "3. Intellectual Property and Copyright",
                "content": "StudyMind AI does not assume the obligation to verify the copyright status of user-uploaded content. The platform operates as a 'hosting provider' under applicable internet laws. A 'Notice and Takedown' mechanism is implemented for copyright infringement reports, and reported content is promptly removed upon valid notification."
            },
            {
                "heading": "4. Content Processing and Data Storage",
                "content": "Uploaded PDF files are analyzed by AI to generate summaries and quizzes. Generated content is original and not a verbatim copy of the source material. Original file text is shortened or deleted from our servers after analysis is complete. The platform does not distribute or share original works."
            },
            {
                "heading": "5. Notice and Takedown",
                "content": "Copyright holders or their authorized representatives may submit a 'Copyright Report' form if they believe uploaded content infringes their rights. Reported content will be removed within 24 hours. Repeat offenders may have their accounts suspended."
            },
            {
                "heading": "6. Credit System and Payments",
                "content": "The platform uses a credit system for AI summary and quiz generation services. Credits represent AI processing capacity, not the sale of copyrighted content. Used credits are non-refundable."
            },
            {
                "heading": "7. Limitation of Liability",
                "content": "StudyMind AI cannot be held responsible for copyright infringements arising from user-uploaded content. The platform responds to infringing content via its Notice and Takedown mechanism as quickly as possible. Accuracy of AI-generated summaries and quizzes is not guaranteed."
            },
            {
                "heading": "8. Changes",
                "content": "StudyMind AI reserves the right to modify these terms of service with prior notice. Current terms are always published on this page."
            }
        ]
    }

@app.get("/api/legal/privacy")
async def get_privacy():
    """Gizlilik politikası"""
    return {
        "title_tr": "Gizlilik Politikası",
        "title_en": "Privacy Policy",
        "last_updated": "2026-03-25",
        "summary_tr": "StudyMind AI, kullanıcı verilerini yalnızca hizmet sunumu amacıyla işler. Yüklenen PDF dosyaları analiz edildikten sonra orijinal metin kısaltılır. Kişisel veriler üçüncü taraflarla paylaşılmaz. Kullanıcılar hesaplarını ve verilerini istedikleri zaman silebilir.",
        "summary_en": "StudyMind AI processes user data solely for service delivery. Original text from uploaded PDFs is shortened after analysis. Personal data is not shared with third parties. Users can delete their accounts and data at any time."
    }


## ============================================================
## v15.3: BEKLEME LİSTESİ (Coming Soon E-posta Toplama)
## ============================================================

class WaitlistEmail(BaseModel):
    email: str

@app.post("/api/waitlist")
async def add_to_waitlist(data: WaitlistEmail, db=Depends(get_db)):
    """Coming Soon sayfasından e-posta topla"""
    safe_email = data.email.lower().strip()
    if not safe_email or "@" not in safe_email:
        raise HTTPException(400, "Geçerli bir e-posta adresi girin")
    
    existing = db.execute("SELECT id FROM waitlist WHERE email=?", (safe_email,)).fetchone()
    if existing:
        return {"message": "Bu e-posta zaten kayıtlı", "already_exists": True}
    
    db.execute("INSERT INTO waitlist(email)VALUES(?)", (safe_email,))
    db.commit()
    return {"message": "Kaydınız alındı!", "already_exists": False}

@app.get("/api/admin/waitlist")
async def get_waitlist(token:str="", db=Depends(get_db)):
    """Admin: Bekleme listesini görüntüle"""
    if not verify_admin(token): raise HTTPException(403)
    rows = db.execute("SELECT * FROM waitlist ORDER BY created_at DESC").fetchall()
    return {"total": len(rows), "emails": [dict(r) for r in rows]}


if __name__=="__main__":
    import uvicorn;uvicorn.run("main:app",host="0.0.0.0",port=8000,reload=True)
