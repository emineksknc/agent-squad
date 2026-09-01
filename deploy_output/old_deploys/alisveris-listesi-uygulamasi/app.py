from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
ma = Marshmallow(app)

# Models

class List(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

    def __init__(self, name):
        self.name = name

# Schemas

class ListSchema(ma.Schema):
    class Meta:
        fields = ('id', 'name')

list_schema = ListSchema()
lists_schema = ListSchema(many=True)

# Routes

@app.route('/list', methods=['POST'])
def add_list():
    name = request.json['name']
    new_list = List(name)
    db.session.add(new_list)
    db.session.commit()
    return list_schema.jsonify(new_list)

@app.route('/list', methods=['PUT'])
def update_list():
    id = request.json['id']
    name = request.json['name']
    list = List.query.get(id)
    if list:
        list.name = name
        db.session.commit()
        return list_schema.jsonify(list)
    else:
        return jsonify({'error': 'List not found'}), 404

@app.route('/list', methods=['DELETE'])
def delete_list():
    id = request.json['id']
    list = List.query.get(id)
    if list:
        db.session.delete(list)
        db.session.commit()
        return list_schema.jsonify(list)
    else:
        return jsonify({'error': 'List not found'}), 404

if __name__ == '__main__':
    db.create_all()
    app.run(port=5137, host='127.0.0.1', debug=True)