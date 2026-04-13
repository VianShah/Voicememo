import { Pinecone } from '@pinecone-database/pinecone';
import path from 'path';
import dotenv from 'dotenv';

// Load .env from project root
dotenv.config({ path: path.resolve(__dirname, '../../../.env') });

async function verifyConnection() {
  const apiKey = process.env.PINECONE_API_KEY;
  const indexName = process.env.PINECONE_INDEX || 'voicememos';

  if (!apiKey) {
    console.error('❌ Error: PINECONE_API_KEY is not set in .env');
    return;
  }

  console.log('🔍 Testing Pinecone Connection...');
  console.log(`📡 Index: ${indexName}`);

  try {
    const pc = new Pinecone({ apiKey });
    
    // 1. Check Index existence and configuration
    const indexDescription = await pc.describeIndex(indexName);
    console.log('✅ Connection Successful!');
    console.log('📊 Index Info:', {
      name: indexDescription.name,
      dimension: indexDescription.dimension,
      metric: indexDescription.metric,
      status: indexDescription.status.state,
      host: indexDescription.host
    });

    if (indexDescription.dimension !== 1024) {
      console.warn(`⚠️ Warning: Index dimension is ${indexDescription.dimension}. For multilingual-e5-large, it should be 1024.`);
    }

    // 2. Test Inference API
    console.log('🧪 Testing Inference API (multilingual-e5-large)...');
    const testEmbedding = await pc.inference.embed({
      model: 'multilingual-e5-large',
      inputs: ['Connection test'],
      parameters: { inputType: 'passage' }
    });
    
    console.log('📥 Received Response:', JSON.stringify(testEmbedding).slice(0, 100) + '...');
    
    if (testEmbedding && testEmbedding.data && testEmbedding.data.length > 0) {
      console.log('✅ Inference API (Embedding) is working!');
      console.log(`📏 Vector length: ${(testEmbedding.data[0] as any).values.length}`);
    } else {
      console.log('❌ Inference API returned empty response.');
    }

  } catch (error: any) {
    console.error('❌ Connection Failed!');
    console.error('Error details:', error.message);
  }
}

verifyConnection();
