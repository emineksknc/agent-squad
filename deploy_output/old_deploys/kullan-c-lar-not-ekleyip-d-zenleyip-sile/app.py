from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///notlar.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
ma = Marshmallow(app)

# Not Model
class Not(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    baslik = db.Column(db.String(100), nullable=False)
    icerik = db.Column(db.Text, nullable=False)
    durum = db.Column(db.Boolean, default=True)

    def __init__(self, baslik, icerik):
        self.baslik = baslik
        self.icerik = icerik

# Not Schema
class NotSchema(ma.Schema):
    class Meta:
        fields = ('id', 'baslik', 'icerik', 'durum')

not_schema = NotSchema()
nots_schema = NotSchema(many=True)

# Create a Not
@app.route('/not', methods=['POST'])
def add_not():
    baslik = request.json['baslik']
    icerik = request.json['icerik']

    yeni_not = Not(baslik, icerik)

    db.session.add(yeni_not)
    db.session.commit()

    return not_schema.jsonify(yeni_not)

# Get All Notes
@app.route('/not', methods=['GET'])
def get_notes():
    all_notes = Not.query.all()
    result = nots_schema.dump(all_notes)
    return jsonify(result)

# Get Single Note
@app.route('/not/<id>', methods=['GET'])
def get_note(id):
    note = Not.query.get(id)
    return not_schema.jsonify(note)

# Update a Note
@app.route('/not/<id>', methods=['PUT'])
def update_note(id):
    note = Not.query.get(id)

    baslik = request.json['baslik']
    icerik = request.json['icerik']

    note.baslik = baslik
    note.icerik = icerik

    db.session.commit()

    return not_schema.jsonify(note)

# Delete a Note
@app.route('/not/<id>', methods=['DELETE'])
def delete_note(id):
    note = Not.query.get(id)

    if note.durum:
        db.session.delete(note)
        db.session.commit()
        return jsonify({'message': 'Not deleted!'}), 200
    else:
        return jsonify({'message': 'Not already deleted!'}), 400

# Run the app
if __name__ == '__main__':
    db.create_all()
    app.run(port=5050, host='127.0.0.1', debug=True)