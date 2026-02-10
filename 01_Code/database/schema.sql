-- USERS TABLE
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    role TEXT CHECK(role IN ('student', 'faculty', 'admin')) NOT NULL,
    status TEXT CHECK(status IN ('active', 'inactive')) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- REVIEWS TABLE
CREATE TABLE reviews (
    review_id TEXT PRIMARY KEY,
    feedback_text TEXT NOT NULL,
    sentiment TEXT CHECK(sentiment IN ('positive', 'neutral', 'negative')),
    is_anonymous INTEGER DEFAULT 1,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- KNOWLEDGE POSTS TABLE
CREATE TABLE knowledge_posts (
    post_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- CHECKLISTS TABLE
CREATE TABLE checklists (
    checklist_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- CHECKLIST ITEMS TABLE
CREATE TABLE checklist_items (
    item_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    status TEXT CHECK(status IN ('pending', 'completed')) DEFAULT 'pending',
    checklist_id TEXT,
    FOREIGN KEY (checklist_id) REFERENCES checklists(checklist_id)
);

-- WHITELIST TABLE
CREATE TABLE whitelists (
    whitelist_id TEXT PRIMARY KEY,
    identifier TEXT NOT NULL,
    role TEXT CHECK(role IN ('faculty', 'admin')),
    validated_by TEXT,
    valid_until DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
