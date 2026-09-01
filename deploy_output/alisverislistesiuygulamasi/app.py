from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash
import re

app = Flask(__name__)

# Dev Notes: Frontend'in ana HTML dosyasýný servis etmek için GET '/' route'u eklenmiþtir.

@app.route('/', methods=['GET'])
def index():
    return jsonify({'message': 'Welcome to the User Registration and Login API'})

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.+[a-zA-Z]{2,}$', email):
        return jsonify({'error': 'Invalid email format'}), 400

    # Simulated database
    users = []
    for user in users:
        if user['email'] == email:
            return jsonify({'error': 'Email already exists'}), 409

    hashed_password = generate_password_hash(password, method='sha256')
    new_user = {'email': email, 'password': hashed_password}
    users.append(new_user)

    return jsonify({'message': 'User registered successfully'}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    # Simulated database
    users = []
    for user in users:
        if user['email'] == email and user['password'] == password:
            return jsonify({'message': 'Login successful'}), 200

    return jsonify({'error': 'Invalid email or password'}), 401

if __name__ == '__main__':
    app.run(port=5183, host='127.0.0.1', debug=True)