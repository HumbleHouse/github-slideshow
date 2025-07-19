from flask import Flask, jsonify, request, send_file
from flask_restful import Api, Resource
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from datetime import datetime
import json
import io

try:
    import barcode
    from barcode.writer import ImageWriter
except ImportError:  # pragma: no cover - library might not be installed
    barcode = None

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///traceability.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
api = Api(app)

class ItemMaster(db.Model):
    __tablename__ = 'item_master'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False)  # raw_material, packaging, finished_good
    unit = db.Column(db.String, nullable=False)

class Lot(db.Model):
    __tablename__ = 'lot'
    lot_id = db.Column(db.String, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('item_master.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    location = db.Column(db.String, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    source_batches = db.Column(db.Text)  # JSON list of parent lot_ids

    item = relationship('ItemMaster')

class Batch(db.Model):
    __tablename__ = 'batch'
    batch_id = db.Column(db.String, primary_key=True)
    formula_name = db.Column(db.String, nullable=False)
    output_lot_id = db.Column(db.String, db.ForeignKey('lot.lot_id'), nullable=False)
    inputs = db.Column(db.Text)  # JSON list of {lot_id, quantity_used}
    created_by = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    output_lot = relationship('Lot')

# Resources
class ItemResource(Resource):
    def post(self):
        data = request.get_json(force=True)
        item = ItemMaster(name=data['name'], type=data['type'], unit=data['unit'])
        db.session.add(item)
        db.session.commit()
        return {'id': item.id}, 201

class LotResource(Resource):
    def post(self):
        data = request.get_json(force=True)
        lot = Lot(
            lot_id=data['lot_id'],
            item_id=data['item_id'],
            quantity=data['quantity'],
            location=data.get('location'),
            source_batches=json.dumps(data.get('source_batches', [])),
        )
        db.session.add(lot)
        db.session.commit()
        return {'lot_id': lot.lot_id}, 201

class BatchResource(Resource):
    def post(self):
        data = request.get_json(force=True)
        inputs = data['inputs']
        # subtract quantities from input lots
        for inp in inputs:
            lot = Lot.query.get(inp['lot_id'])
            if not lot:
                return {'error': f"input lot {inp['lot_id']} not found"}, 404
            if lot.quantity < inp['quantity_used']:
                return {'error': f"not enough quantity in lot {inp['lot_id']}"}, 400
            lot.quantity -= inp['quantity_used']
        # create output lot
        output_lot = Lot(
            lot_id=data['output_lot_id'],
            item_id=data['item_id'],
            quantity=data['output_quantity'],
            location=data.get('location'),
            source_batches=json.dumps([i['lot_id'] for i in inputs]),
        )
        db.session.add(output_lot)
        batch = Batch(
            batch_id=data['batch_id'],
            formula_name=data['formula_name'],
            output_lot_id=output_lot.lot_id,
            inputs=json.dumps(inputs),
            created_by=data['created_by'],
        )
        db.session.add(batch)
        db.session.commit()
        return {'batch_id': batch.batch_id}, 201

class LotDetailResource(Resource):
    def get(self, lot_id):
        lot = Lot.query.get(lot_id)
        if not lot:
            return {'error': 'lot not found'}, 404
        result = {
            'lot_id': lot.lot_id,
            'item_id': lot.item_id,
            'quantity': lot.quantity,
            'location': lot.location,
            'created_at': lot.created_at.isoformat(),
            'source_batches': json.loads(lot.source_batches or '[]'),
        }
        return result

class BarcodeResource(Resource):
    def get(self, lot_id):
        if barcode is None:
            return {'error': 'barcode library not installed'}, 500
        code128 = barcode.get('code128', lot_id, writer=ImageWriter())
        buffer = io.BytesIO()
        code128.write(buffer)
        buffer.seek(0)
        return send_file(buffer, mimetype='image/png')

api.add_resource(ItemResource, '/item')
api.add_resource(LotResource, '/lot')
api.add_resource(BatchResource, '/batch')
api.add_resource(LotDetailResource, '/lot/<string:lot_id>')
api.add_resource(BarcodeResource, '/barcode/<string:lot_id>')

if __name__ == '__main__':
    db.create_all()
    app.run(debug=True)
