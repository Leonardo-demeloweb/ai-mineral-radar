// ============================================
// MongoDB Initialization Script
// ============================================
// Creates database, collections, and indexes

// Switch to supplyradar database
db = db.getSiblingDB('supplyradar');

// Create collections with validation
print('Creating collections...');

// ==================== empresas ====================
db.createCollection('empresas', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['cnpj', 'razao_social', 'plano', 'created_at'],
      properties: {
        cnpj: { bsonType: 'string' },
        razao_social: { bsonType: 'string' },
        nome_fantasia: { bsonType: 'string' },
        plano: { enum: ['free', 'pro', 'enterprise'] },
      }
    }
  }
});

// ==================== usuarios ====================
db.createCollection('usuarios', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['empresa_id', 'email', 'nome', 'role', 'created_at'],
      properties: {
        email: { bsonType: 'string' },
        nome: { bsonType: 'string' },
        role: { enum: ['viewer', 'editor', 'admin'] },
      }
    }
  }
});

// ==================== obras ====================
db.createCollection('obras');

// ==================== estudos ====================
db.createCollection('estudos');

// ==================== pesquisas ====================
db.createCollection('pesquisas');

// ==================== uploads ====================
db.createCollection('uploads');

// ==================== rotas_calculadas ====================
db.createCollection('rotas_calculadas');

// ==================== audit_log ====================
db.createCollection('audit_log');

// ==================== Create Indexes ====================
print('Creating indexes...');

// empresas indexes
db.empresas.createIndex({ 'cnpj': 1 }, { unique: true });

// usuarios indexes
db.usuarios.createIndex({ 'empresa_id': 1 });
db.usuarios.createIndex({ 'email': 1 }, { unique: true });

// obras indexes
db.obras.createIndex({ 'empresa_id': 1, 'status': 1 });
db.obras.createIndex({ 'localizacao': '2dsphere' });

// estudos indexes
db.estudos.createIndex({ 'obra_id': 1, 'status': 1 });
db.estudos.createIndex({ 'usuario_criador_id': 1 });

// pesquisas indexes
db.pesquisas.createIndex({ 'estudo_id': 1, 'status': 1 });
db.pesquisas.createIndex({ 'jazida_id': 1 });

// uploads indexes
db.uploads.createIndex({ 'estudo_id': 1 });

// rotas_calculadas indexes
db.rotas_calculadas.createIndex({ 'pesquisa_id': 1 });
db.rotas_calculadas.createIndex(
  { 'valido_ate': 1 }, 
  { expireAfterSeconds: 0 }  // TTL index
);

// audit_log indexes
db.audit_log.createIndex({ 'empresa_id': 1, 'created_at': -1 });
db.audit_log.createIndex({ 'usuario_id': 1, 'created_at': -1 });
db.audit_log.createIndex(
  { 'created_at': 1 }, 
  { expireAfterSeconds: 7776000 }  // 90 days TTL
);

print('MongoDB initialization complete!');
