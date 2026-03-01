# FitnessJournal Bot v3.6.0 - Guia Técnico

## 🎯 VISÃO GERAL

Este documento complementa o CHANGELOG com detalhes técnicos de implementação e exemplos práticos de uso das novas features.

---

## 📚 ARQUITETURA DAS NOVAS FEATURES

### 1. RETRY LOGIC - Exponential Backoff

#### Conceito
O retry logic implementa tentativas automáticas com delays crescentes quando uma chamada ao Gemini falha temporariamente.

#### Implementação Detalhada

```python
# Configuração
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # segundos

async def call_gemini_with_retry(prompt: str, timeout: int = 30) -> Any:
    """
    v3.6: Chama Gemini com retry logic e exponential backoff.
    
    Flow:
    1. Tenta chamar Gemini
    2. Se timeout/erro → aguarda RETRY_DELAYS[attempt]
    3. Repete até MAX_RETRIES
    4. Se todas falharem → raise exception
    """
    
    # Check circuit breaker primeiro
    if circuit_breaker.is_open():
        raise CircuitBreakerOpen("Serviço indisponível")
    
    # Check cache
    cached = response_cache.get(prompt)
    if cached:
        return CachedResponse(cached[0])
    
    # Retry loop
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Gemini attempt {attempt + 1}/{MAX_RETRIES}")
            
            response = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, prompt),
                timeout=timeout
            )
            
            # Sucesso
            circuit_breaker.record_success()
            response_cache.set(prompt, response.text)
            return response
            
        except asyncio.TimeoutError:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.info(f"Retrying after {delay}s...")
                await asyncio.sleep(delay)
                continue
            else:
                circuit_breaker.record_failure()
                raise GeminiTimeoutError(f"Failed after {MAX_RETRIES} attempts")
```

#### Timing Example

**Cenário:** 2 timeouts, sucesso na 3ª tentativa

```
T=0s:   Attempt 1 → Timeout após 30s
T=30s:  Wait 2s
T=32s:  Attempt 2 → Timeout após 30s
T=62s:  Wait 4s
T=66s:  Attempt 3 → SUCCESS
Total: 66 segundos (vs 30s sem retry)
```

#### Quando Usar
- ✅ Timeouts transitórios
- ✅ Network blips
- ✅ API rate limiting temporário
- ❌ Erros de autenticação (não retryable)
- ❌ Prompt inválido (não retryable)

---

### 2. CIRCUIT BREAKER - Fail Fast Pattern

#### Conceito
O circuit breaker previne cascading failures ao "abrir" após múltiplas falhas consecutivas, rejeitando pedidos temporariamente.

#### Estados

```
┌─────────┐
│ CLOSED  │ ←──────────────┐
│ Normal  │                │
└────┬────┘                │
     │                     │
     │ 5 failures          │ 1 success
     ↓                     │
┌─────────┐                │
│  OPEN   │                │
│ Reject  │                │
└────┬────┘                │
     │                     │
     │ 60s timeout         │
     ↓                     │
┌──────────┐               │
│HALF_OPEN │───────────────┘
│Try once  │
└──────────┘
```

#### Implementação

```python
class CircuitBreaker:
    def __init__(self):
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'closed'
    
    def record_success(self):
        """Reset após sucesso"""
        self.failure_count = 0
        self.state = 'closed'
    
    def record_failure(self):
        """Incrementa contagem, abre se threshold"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            self.state = 'open'
            logger.warning(f"Circuit breaker OPENED")
    
    def is_open(self) -> bool:
        """Verifica estado"""
        if self.state == 'closed':
            return False
        
        if self.state == 'open':
            # Transição para half_open após timeout
            elapsed = time.time() - self.last_failure_time
            if elapsed > CIRCUIT_BREAKER_TIMEOUT:
                self.state = 'half_open'
                return False
            return True
        
        return False  # half_open permite tentativa
```

#### Timeline Example

```
T=0:    Request 1 → FAIL (count=1)
T=1:    Request 2 → FAIL (count=2)
T=2:    Request 3 → FAIL (count=3)
T=3:    Request 4 → FAIL (count=4)
T=4:    Request 5 → FAIL (count=5) → OPEN
T=5-64: All requests → REJECTED (CircuitBreakerOpen)
T=65:   State → HALF_OPEN
T=66:   Request → SUCCESS → CLOSED
T=67+:  Normal operation
```

#### Benefícios
1. **Fail Fast**: Rejeita imediatamente quando serviço está down
2. **Recovery**: Tenta automaticamente após timeout
3. **Protection**: Evita sobrecarregar serviço já stressado

---

### 3. RESPONSE CACHE - LRU Cache

#### Conceito
Cache in-memory de respostas do Gemini para reduzir chamadas API redundantes.

#### Implementação

```python
class ResponseCache:
    def __init__(self, max_size: int = 100):
        self.cache = {}           # {hash: (response, timestamp)}
        self.access_times = {}    # {hash: last_access}
    
    def _hash_prompt(self, prompt: str) -> str:
        """SHA-256 hash para key"""
        return hashlib.sha256(prompt.encode()).hexdigest()
    
    def get(self, prompt: str) -> Optional[Tuple[str, float]]:
        """Retorna resposta se cached e não expirado"""
        key = self._hash_prompt(prompt)
        
        if key not in self.cache:
            return None
        
        response, timestamp = self.cache[key]
        age = time.time() - timestamp
        
        # Check TTL
        if age > CACHE_TTL_SECONDS:
            del self.cache[key]
            del self.access_times[key]
            return None
        
        # Update access time (LRU)
        self.access_times[key] = time.time()
        logger.info(f"Cache HIT (age={age:.1f}s)")
        return response, age
    
    def set(self, prompt: str, response: str):
        """Armazena no cache com LRU eviction"""
        key = self._hash_prompt(prompt)
        
        # LRU eviction se cheio
        if len(self.cache) >= self.max_size:
            oldest = min(self.access_times.keys(), 
                        key=lambda k: self.access_times[k])
            del self.cache[oldest]
            del self.access_times[oldest]
        
        self.cache[key] = (response, time.time())
        self.access_times[key] = time.time()
```

#### Cache Hit Example

```
User A: "/analyze" com feeling "Cansado" → Gemini call → Cache SET
User B: "/analyze" com feeling "Cansado" → Cache HIT (instant)
User C: "/analyze" com feeling "Cansado" → Cache HIT (instant)

Total API calls: 1 (vs 3 sem cache)
Savings: 66% reduction
```

#### TTL Strategy

```
CACHE_TTL_SECONDS = 60

Rationale:
- Análises mudam lentamente
- 60s é suficiente para burst de users
- Evita dados muito desatualizados
- Balance entre savings e freshness
```

#### Cache Key Strategy

```python
# ERRADO: Usar prompt direto
cache[prompt] = response  # Collisions fáceis

# CORRETO: Hash SHA-256
key = hashlib.sha256(prompt.encode()).hexdigest()
cache[key] = response  # Unique, deterministic
```

---

### 4. RATE LIMITER - Per-User Sliding Window

#### Conceito
Limita número de requisições por utilizador num intervalo de tempo para prevenir abuse.

#### Implementação

```python
class RateLimiter:
    def __init__(self):
        # user_id → [timestamp1, timestamp2, ...]
        self.requests = defaultdict(list)
    
    def check_rate_limit(self, user_id: int) -> Tuple[bool, int]:
        """
        Verifica se user pode fazer pedido.
        
        Returns:
            (is_allowed, current_count)
        """
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW
        
        # Cleanup old requests (sliding window)
        self.requests[user_id] = [
            ts for ts in self.requests[user_id]
            if ts > window_start
        ]
        
        current = len(self.requests[user_id])
        
        # Check limit
        if current >= RATE_LIMIT_MAX_REQUESTS:
            return False, current
        
        return True, current
    
    def record_request(self, user_id: int):
        """Regista nova request"""
        self.requests[user_id].append(time.time())
```

#### Sliding Window Example

```
Config: 10 requests / 60s

User Timeline:
T=0:   Request 1  ✅ (1/10)
T=5:   Request 2  ✅ (2/10)
T=10:  Request 3  ✅ (3/10)
...
T=50:  Request 10 ✅ (10/10)
T=55:  Request 11 ❌ REJECTED (10/10)
T=61:  Request 12 ✅ (9/10) ← Request 1 expired
```

#### Usage Pattern

```python
# Em cada handler que chama Gemini:

async def handle_feeling(update, context):
    user_id = update.effective_user.id
    
    # Check rate limit
    is_allowed, count = rate_limiter.check_rate_limit(user_id)
    
    if not is_allowed:
        await update.message.reply_text(
            f"⏱️ Limite atingido ({count} requests).\n"
            f"Aguarda 1 minuto."
        )
        return
    
    # Record request
    rate_limiter.record_request(user_id)
    
    # Proceed with Gemini call...
```

---

## 🔄 INTEGRAÇÃO DAS FEATURES

### Flow Completo de Uma Request

```
1. USER: Envia mensagem
   ↓
2. RATE LIMITER: Check if allowed
   ├─ NO → Reject com mensagem
   └─ YES → Continue
   ↓
3. RATE LIMITER: Record request
   ↓
4. CIRCUIT BREAKER: Check state
   ├─ OPEN → Reject (serviço down)
   └─ CLOSED/HALF_OPEN → Continue
   ↓
5. CACHE: Check for cached response
   ├─ HIT → Return cached (instant)
   └─ MISS → Continue to Gemini
   ↓
6. RETRY LOGIC: Call Gemini with retries
   ├─ Attempt 1 → Timeout
   ├─ Wait 2s
   ├─ Attempt 2 → Timeout
   ├─ Wait 4s
   └─ Attempt 3 → SUCCESS
   ↓
7. CIRCUIT BREAKER: Record success
   ↓
8. CACHE: Store response
   ↓
9. USER: Receive response
```

### Exemplo Real de Timeline

**Cenário:** User faz análise pela primeira vez

```
T=0.0s:   User envia mensagem
T=0.1s:   Rate limiter: OK (1/10)
T=0.1s:   Circuit breaker: CLOSED (OK)
T=0.1s:   Cache: MISS
T=0.2s:   Gemini attempt 1 START
T=30.2s:  Gemini attempt 1 TIMEOUT
T=32.2s:  Gemini attempt 2 START
T=35.5s:  Gemini attempt 2 SUCCESS
T=35.5s:  Circuit breaker: record_success()
T=35.6s:  Cache: SET response
T=35.7s:  User recebe resposta

Total: 35.7 segundos
```

**Cenário:** Outro user faz mesma análise 10s depois

```
T=0.0s:   User B envia mensagem idêntica
T=0.1s:   Rate limiter: OK (1/10)
T=0.1s:   Circuit breaker: CLOSED (OK)
T=0.1s:   Cache: HIT (age=10s)
T=0.2s:   User B recebe resposta

Total: 0.2 segundos (175x mais rápido!)
```

---

## 📊 MONITORING & OBSERVABILITY

### Métricas Expostas

#### Via `/stats` Command

```
📊 ESTATÍSTICAS:

👥 Users: 42

Por tipo:
  • plan: 156
  • adherence: 89
  • activity_analysis: 234

🔌 Circuit Breaker:
  Estado: closed
  Falhas: 0/5

💾 Cache:
  Entradas: 47/100
```

#### Via Logs

```python
# Retry logs
logger.info(f"Gemini attempt {attempt + 1}/{MAX_RETRIES}")
logger.info(f"Retrying after {delay}s...")

# Circuit breaker logs
logger.warning(f"Circuit breaker OPENED after {count} failures")
logger.info("Circuit breaker: transitioning to HALF_OPEN")

# Cache logs
logger.info(f"Cache HIT (age={age:.1f}s)")
logger.info(f"Cache SET (size={len(self.cache)})")

# Rate limit logs
logger.warning(f"Rate limit EXCEEDED for user {user_id}: {count}/{max}")
```

### Alerting Recommendations

**Critical Alerts:**
```
- Circuit breaker OPEN > 5 min
- Retry success rate < 50%
- Cache hit rate < 10%
- Multiple users hitting rate limit
```

**Warning Alerts:**
```
- Circuit breaker opens > 3/hour
- Average retry count > 2
- Cache evictions > 10/min
- Rate limit hits > 5/user/hour
```

---

## 🧪 TESTING GUIDE

### Unit Test Examples

```python
import pytest
from main import CircuitBreaker, ResponseCache, RateLimiter

def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker()
    
    # Record 4 failures
    for i in range(4):
        cb.record_failure()
        assert not cb.is_open()
    
    # 5th failure should open
    cb.record_failure()
    assert cb.is_open()

def test_cache_ttl_expiration():
    cache = ResponseCache()
    cache.set("test prompt", "test response")
    
    # Should hit immediately
    result = cache.get("test prompt")
    assert result is not None
    
    # Mock time passage
    time.sleep(61)
    
    # Should miss after TTL
    result = cache.get("test prompt")
    assert result is None

def test_rate_limiter_sliding_window():
    limiter = RateLimiter()
    user_id = 123
    
    # Fill window
    for i in range(10):
        allowed, count = limiter.check_rate_limit(user_id)
        assert allowed
        limiter.record_request(user_id)
    
    # Should reject 11th
    allowed, count = limiter.check_rate_limit(user_id)
    assert not allowed
```

### Integration Test Scenarios

**Scenario 1: Retry Success**
```bash
1. Mock Gemini to timeout 2x, succeed 3rd
2. Call analyze_command()
3. Assert: Response received after ~6s
4. Assert: 3 attempts logged
```

**Scenario 2: Circuit Breaker Opens**
```bash
1. Mock Gemini to always timeout
2. Call analyze_command() 5x
3. Assert: 6th call rejected immediately
4. Assert: CircuitBreakerOpen exception
```

**Scenario 3: Cache Hit**
```bash
1. Call analyze_command() with prompt A
2. Call analyze_command() with same prompt A
3. Assert: 2nd call instant (<1s)
4. Assert: "Cache HIT" in logs
```

**Scenario 4: Rate Limit**
```bash
1. As user X, make 10 analyze calls
2. Make 11th call
3. Assert: Rejection message received
4. Wait 60s
5. Make 12th call
6. Assert: Success
```

---

## 🚨 TROUBLESHOOTING

### Issue: Circuit Breaker Stuck Open

**Symptoms:**
- All requests rejected
- "Circuit breaker is OPEN" errors
- `/stats` shows `state: open`

**Diagnosis:**
```python
# Check logs:
grep "Circuit breaker" /var/log/bot.log | tail -20

# Expected pattern:
# "Circuit breaker OPENED" → wait 60s → "transitioning to HALF_OPEN"
```

**Solutions:**
1. Wait 60s for automatic recovery
2. Restart bot to force reset
3. Check if Gemini API is actually down

---

### Issue: Low Cache Hit Rate

**Symptoms:**
- `/stats` shows `Entradas: 2/100`
- Most calls hitting Gemini
- Slow response times

**Diagnosis:**
```python
# Check if prompts are slightly different:
grep "Cache MISS" /var/log/bot.log | wc -l
grep "Cache HIT" /var/log/bot.log | wc -l

# Hit rate should be > 30%
```

**Solutions:**
1. Normalize prompts before caching (trim whitespace)
2. Increase TTL if data doesn't change much
3. Increase cache size if evictions frequent

---

### Issue: Rate Limit Too Strict

**Symptoms:**
- Legit users being blocked
- Many "Rate limit EXCEEDED" in logs
- User complaints

**Diagnosis:**
```python
# Check rate limit hits per user:
grep "Rate limit EXCEEDED" /var/log/bot.log | \
  awk '{print $NF}' | sort | uniq -c

# If many different users → limit too strict
```

**Solutions:**
```python
# Adjust constants:
RATE_LIMIT_WINDOW = 120  # 60 → 120 seconds
RATE_LIMIT_MAX_REQUESTS = 15  # 10 → 15 requests

# Or implement user tiers:
if user_is_premium(user_id):
    limit = 20
else:
    limit = 10
```

---

## 📖 BEST PRACTICES

### 1. Circuit Breaker

**DO:**
- ✅ Use for external service calls only
- ✅ Set threshold based on error budget
- ✅ Monitor state transitions
- ✅ Provide clear user messaging

**DON'T:**
- ❌ Use for database calls
- ❌ Set threshold too low (< 3)
- ❌ Leave stuck open forever
- ❌ Ignore when it opens frequently

---

### 2. Retry Logic

**DO:**
- ✅ Use exponential backoff
- ✅ Limit max retries (3-5)
- ✅ Add jitter for distributed systems
- ✅ Log each attempt

**DON'T:**
- ❌ Retry non-idempotent operations
- ❌ Use linear delays
- ❌ Retry forever
- ❌ Retry auth errors

---

### 3. Caching

**DO:**
- ✅ Use deterministic keys (hash)
- ✅ Set appropriate TTL
- ✅ Implement LRU eviction
- ✅ Measure hit rate

**DON'T:**
- ❌ Cache sensitive data
- ❌ Use unbounded cache
- ❌ Cache forever
- ❌ Ignore invalidation

---

### 4. Rate Limiting

**DO:**
- ✅ Use sliding window
- ✅ Track per user/IP
- ✅ Provide clear messaging
- ✅ Allow burst within limit

**DON'T:**
- ❌ Use fixed window
- ❌ Block forever
- ❌ Silent drops
- ❌ Same limit for all users

---

## 🎓 LEARNING RESOURCES

### Patterns Implemented

1. **Circuit Breaker**: Michael Nygard - "Release It!"
2. **Retry with Backoff**: AWS Architecture Blog
3. **LRU Cache**: Classic CS algorithm
4. **Sliding Window**: Rate limiting best practice

### Further Reading

- [Martin Fowler - Circuit Breaker](https://martinfowler.com/bliki/CircuitBreaker.html)
- [Google SRE Book - Handling Overload](https://sre.google/sre-book/handling-overload/)
- [AWS - Exponential Backoff And Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
- [Redis - Rate Limiting](https://redis.io/docs/manual/patterns/rate-limiter/)

---

## ✅ CONCLUSION

A versão 3.6.0 implementa **4 patterns enterprise-grade** que transformam o bot de um serviço frágil numa aplicação resiliente e escalável.

**Key Takeaways:**
1. Retry logic reduz falhas transitórias
2. Circuit breaker protege de cascading failures
3. Cache melhora performance drasticamente
4. Rate limiting previne abuse

**Next Steps:**
1. Deploy v3.6.0
2. Monitor metrics por 1 semana
3. Ajustar constants se necessário
4. Adicionar mais patterns conforme necessário

---

**Versão Documento:** 1.0  
**Data:** 01 Março 2026  
**Autor:** Code Review v3.6.0