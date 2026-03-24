# Quiz Answers

**1. Make sure you have crawled data available and also uploaded to GitHub.**
(Completed)

**2. Find a word that appears on multiple different URLs (e.g., python, program, page).**
Write down the word you chose: **page**

**3. For that word, copy 3 entries from the file (each line is: word url origin depth frequency):**
- Entry 1: **page https://www.zyte.com/products/web-scraping-copilot http://quotes.toscrape.com/ 2 6**
- Entry 2: **page https://www.zyte.com/data-types/product-scraper http://quotes.toscrape.com/ 2 3**
- Entry 3: **page https://www.zyte.com/data-types/news-articles-scraper http://quotes.toscrape.com/ 2 4**

**4. Now search for that word via the API:**
GET http://localhost:3600/search?query=page&sortBy=relevance

**5. Write down the #1 result's URL and relevance_score:**
- URL: **https://www.zyte.com/zyte-api/ai-extraction**
- relevance_score: **1070**

**6. Now manually calculate the score for each of your 3 entries using the formula:**
score = (frequency x 10) + 1000 (exact match bonus) - (depth x 5)

- Entry 1 score: ( **6** x 10 ) + 1000 - ( **2** x 5 ) = **1050**
- Entry 2 score: ( **3** x 10 ) + 1000 - ( **2** x 5 ) = **1020**
- Entry 3 score: ( **4** x 10 ) + 1000 - ( **2** x 5 ) = **1030**

**7. Does the highest score you calculated match the API's #1 result? Yes / No:**
**No** (My local sample's highest score is 1050, but the API searched the entire database and found a better global match with a score of 1070).

**8. How could you enhance the process in a Chain-of-Thought Manner. Explain.**
To enhance this search and ranking process using a Chain-of-Thought (CoT) approach, we could transition from a rigid, hardcoded mathematical formula to an LLM-driven semantic evaluation. Instead of immediately assigning a final score based purely on keyword frequency and depth, a CoT-prompted AI agent would be instructed to explicitly reason through multiple relevancy factors step-by-step before determining the final rank. 

The enhanced process would look like this:
1. **Contextual Analysis (Step 1):** The system first evaluates the intent behind the user's query rather than just matching the literal string. 
2. **Content Evaluation (Step 2):** It then examines the occurrences of the word. Is the word "page" referring to a web page, a book page, or paging someone? Frequency alone doesn't guarantee quality context.
3. **Source Authority & Depth (Step 3):** The system assesses if a depth-2 page is actually a highly specific, valuable article, rather than strictly penalizing it for being further from the origin.
4. **Final Scoring (Step 4):** After generating this step-by-step reasoning rationale, the system synthesizes a dynamic relevance score. 

By forcing the system to "think aloud" and explain *why* a document matches a query contextually before scoring it, we eliminate "keyword stuffing" bias and significantly improve the quality and semantic accuracy of the search results.