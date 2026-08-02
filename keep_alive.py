from flask import Flask, render_template_string
from threading import Thread

app = Flask(__name__)

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Panel</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f0f8ff; /* Light blue background */
            color: #333;
            margin: 0;
            padding: 20px;
        }
        h2 {
            color: #007bff;
            text-align: center;
            margin-bottom: 20px;
        }
        .card {
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 10px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #555;
        }
        input {
            width: 100%;
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 8px;
            box-sizing: border-box;
            font-size: 16px;
        }
        input:focus {
            border-color: #007bff;
            outline: none;
        }
        .btn {
            background: #007bff;
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 8px;
            width: 100%;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: background 0.3s;
        }
        .btn:active {
            background: #0056b3;
        }
    </style>
</head>
<body>
    <h2>🛠️ Admin Panel</h2>
    
    <div class="card">
        <h3>➕ Add Task Details</h3>
        <div class="form-group">
            <label>Platform Name (e.g., Instagram):</label>
            <input type="text" id="platform" placeholder="Platform">
        </div>
        <div class="form-group">
            <label>Username:</label>
            <input type="text" id="username" placeholder="Username">
        </div>
        <div class="form-group">
            <label>Password:</label>
            <input type="text" id="password" placeholder="Password">
        </div>
        <button class="btn" onclick="saveData()">Save Credentials</button>
    </div>

    <script>
        let tg = window.Telegram.WebApp;
        tg.expand();
        tg.setHeaderColor('#007bff');

        function saveData() {
            let platform = document.getElementById('platform').value;
            let user = document.getElementById('username').value;
            let pass = document.getElementById('password').value;
            
            if(platform && user && pass) {
                // Here you would typically send a request to your backend to save the data
                tg.showAlert(`Successfully added credentials for ${platform}!\\nUser: ${user}`);
                document.getElementById('platform').value = '';
                document.getElementById('username').value = '';
                document.getElementById('password').value = '';
            } else {
                tg.showAlert("⚠️ Please fill in all fields!");
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return "Bot is Alive and Running 24/7!"

@app.route('/admin')
def admin_panel():
    return render_template_string(ADMIN_HTML)

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
