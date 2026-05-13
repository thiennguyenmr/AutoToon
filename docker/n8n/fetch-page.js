// Fetch page using browserless/chromium HTTP API
const http = require('http');

const url = process.argv[2] || 'https://www.bachhoaxanh.com';

const postData = JSON.stringify({
  url: url,
  gotoOptions: { waitUntil: 'networkidle2', timeout: 30000 }
});

const req = http.request({
  hostname: 'chromium',
  port: 3000,
  path: '/content',
  method: 'POST',
  headers: { 'Content-Type': 'application/json' }
}, (res) => {
  let data = '';
  res.on('data', (c) => data += c);
  res.on('end', () => {
    if (res.statusCode !== 200) {
      console.error('Browserless error:', data);
      process.exit(1);
    }
    const output = JSON.stringify({ statusCode: 200, html: data, url: url });
    process.stdout.write(output);
  });
});

req.on('error', (err) => {
  console.error(err.message);
  process.exit(1);
});

req.write(postData);
req.end();
