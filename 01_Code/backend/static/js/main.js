document.addEventListener('DOMContentLoaded', () => {
    if (window.lucide) {
        window.lucide.createIcons();
    }

    const root = document.documentElement;
    const themeToggle = document.getElementById('themeToggle');
    const savedTheme = localStorage.getItem('clarifai-theme');

    if (savedTheme === 'light') {
        root.classList.add('light');
    }

    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            root.classList.toggle('light');
            localStorage.setItem('clarifai-theme', root.classList.contains('light') ? 'light' : 'dark');
        });
    }

    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            window.location.reload();
        }
    });

    document.querySelectorAll('.flash.success, .flash.warning').forEach((flash) => {
        window.setTimeout(() => {
            flash.classList.add('is-dismissing');
            window.setTimeout(() => flash.remove(), 460);
        }, 4000);
    });

    document.querySelectorAll('[data-open-modal]').forEach((button) => {
        button.addEventListener('click', () => {
            const target = document.getElementById(button.dataset.openModal);
            if (target) {
                target.classList.add('active');
            }
        });
    });

    document.querySelectorAll('[data-close-modal]').forEach((button) => {
        button.addEventListener('click', () => {
            const target = button.closest('.modal');
            if (target) {
                target.classList.remove('active');
            }
        });
    });

    document.querySelectorAll('.modal').forEach((modal) => {
        modal.addEventListener('click', (event) => {
            if (event.target === modal) {
                modal.classList.remove('active');
            }
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') {
            return;
        }
        document.querySelectorAll('.modal.active').forEach((modal) => {
            modal.classList.remove('active');
        });
    });

    document.querySelectorAll('[data-toggle-password]').forEach((button) => {
        button.addEventListener('click', () => {
            const fieldId = button.getAttribute('data-toggle-password');
            const input = document.getElementById(fieldId);
            if (!input) {
                return;
            }

            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            const nextLabel = show ? 'Hide password' : 'Show password';
            button.setAttribute('title', nextLabel);
            button.setAttribute('aria-label', nextLabel);

            if (button.querySelector('svg') && window.lucide) {
                button.innerHTML = show ? '<i data-lucide="eye-off"></i>' : '<i data-lucide="eye"></i>';
                window.lucide.createIcons();
            } else if (!button.querySelector('svg')) {
                button.textContent = show ? 'Hide' : 'Peek';
            }
        });
    });

    const reviewSessionInputs = Array.from(
        document.querySelectorAll('input[type="datetime-local"][data-review-session-time]')
    );
    if (reviewSessionInputs.length) {
        const validateReviewSessionInput = (input) => {
            if (!input || !input.value) {
                if (input) {
                    input.setCustomValidity('');
                }
                return true;
            }

            const [datePart, timePart] = input.value.split('T');
            if (!datePart || !timePart) {
                input.setCustomValidity('Please provide a valid class session date and time.');
                return false;
            }

            const selectedDay = new Date(`${datePart}T00:00:00`).getDay();
            if (selectedDay === 0) {
                input.setCustomValidity('Class session date cannot be on Sunday.');
                return false;
            }

            const [hourText, minuteText] = timePart.split(':');
            const hour = Number.parseInt(hourText || '0', 10);
            const minute = Number.parseInt(minuteText || '0', 10);
            if (hour < 8 || hour > 17 || (hour === 17 && minute > 0)) {
                input.setCustomValidity('Class session time must be between 08:00 AM and 05:00 PM (IST).');
                return false;
            }

            input.setCustomValidity('');
            return true;
        };

        reviewSessionInputs.forEach((input) => {
            input.addEventListener('change', () => {
                validateReviewSessionInput(input);
            });
            input.addEventListener('input', () => {
                validateReviewSessionInput(input);
            });

            const form = input.closest('form');
            if (!form) {
                return;
            }
            form.addEventListener('submit', (event) => {
                if (!validateReviewSessionInput(input)) {
                    event.preventDefault();
                    input.reportValidity();
                }
            });
        });
    }

    const wizardForm = document.getElementById('knowledgeWizardForm');
    if (wizardForm) {
        const stepPanels = Array.from(wizardForm.querySelectorAll('[data-step-panel]'));
        const stepLabel = document.getElementById('wizardStepLabel');
        const stepNodes = Array.from(wizardForm.parentElement.querySelectorAll('.wizard-node'));
        const stepDots = Array.from(wizardForm.querySelectorAll('.wizard-dots span'));
        const backButton = document.getElementById('wizardBack');
        const nextButton = document.getElementById('wizardNext');
        const publishButton = document.getElementById('wizardPublish');
        const addStepButton = document.getElementById('addSolutionStep');
        const stepsWrap = document.getElementById('solutionStepsWrap');
        const stepsHidden = document.getElementById('solutionStepsHidden');
        const tagInput = document.getElementById('wizardTagInput');
        const totalSteps = stepPanels.length;
        let currentStep = 1;

        const refreshStepInputs = () => {
            const stepInputs = Array.from(document.querySelectorAll('.wizard-step-input'));
            stepInputs.forEach((input, index) => {
                input.setAttribute('data-step-index', String(index + 1));
                const badge = input.closest('.wizard-step-item')?.querySelector('span');
                if (badge) {
                    badge.textContent = String(index + 1);
                }
            });
        };

        const collectSolutionSteps = () => {
            const stepInputs = Array.from(document.querySelectorAll('.wizard-step-input'));
            const values = stepInputs.map((input) => input.value.trim()).filter(Boolean);
            stepsHidden.value = values.join('\n');
            return values;
        };

        const validateStep = () => {
            const panel = stepPanels[currentStep - 1];
            const requiredFields = Array.from(panel.querySelectorAll('input[required], textarea[required], select[required]'));
            for (const field of requiredFields) {
                if (!field.checkValidity()) {
                    field.reportValidity();
                    return false;
                }
            }

            if (currentStep === 3) {
                const values = collectSolutionSteps();
                if (!values.length) {
                    const firstStepInput = panel.querySelector('.wizard-step-input');
                    if (firstStepInput) {
                        firstStepInput.setCustomValidity('Please add at least one solution step.');
                        firstStepInput.reportValidity();
                        firstStepInput.setCustomValidity('');
                    }
                    return false;
                }
            }

            return true;
        };

        const syncWizardUi = () => {
            stepPanels.forEach((panel, index) => {
                panel.classList.toggle('active', index + 1 === currentStep);
            });

            stepNodes.forEach((node, index) => {
                node.classList.toggle('active', index + 1 <= currentStep);
            });

            stepDots.forEach((dot, index) => {
                dot.classList.toggle('active', index + 1 === currentStep);
            });

            if (stepLabel) {
                stepLabel.textContent = `Step ${currentStep} of ${totalSteps}`;
            }

            backButton.style.visibility = currentStep === 1 ? 'hidden' : 'visible';
            nextButton.style.display = currentStep === totalSteps ? 'none' : 'inline-flex';
            publishButton.style.display = currentStep === totalSteps ? 'inline-flex' : 'none';
        };

        if (addStepButton && stepsWrap) {
            addStepButton.addEventListener('click', () => {
                const item = document.createElement('div');
                item.className = 'wizard-step-item';
                item.innerHTML = '<span></span><input type="text" class="wizard-step-input" placeholder="Add step">';
                stepsWrap.appendChild(item);
                refreshStepInputs();
            });
        }

        document.querySelectorAll('[data-quick-tag]').forEach((chip) => {
            chip.addEventListener('click', () => {
                if (!tagInput) {
                    return;
                }
                const current = tagInput.value
                    .split(',')
                    .map((token) => token.trim())
                    .filter(Boolean);
                const nextTag = chip.getAttribute('data-quick-tag');
                if (!nextTag || current.includes(nextTag)) {
                    return;
                }
                current.push(nextTag);
                tagInput.value = current.join(', ');
                chip.classList.add('active');
            });
        });

        if (nextButton) {
            nextButton.addEventListener('click', () => {
                if (!validateStep()) {
                    return;
                }
                currentStep = Math.min(currentStep + 1, totalSteps);
                syncWizardUi();
            });
        }

        if (backButton) {
            backButton.addEventListener('click', () => {
                currentStep = Math.max(currentStep - 1, 1);
                syncWizardUi();
            });
        }

        wizardForm.addEventListener('submit', (event) => {
            collectSolutionSteps();
            if (!validateStep()) {
                event.preventDefault();
            }
        });

        refreshStepInputs();
        syncWizardUi();
    }

    const sidebar = document.getElementById('appSidebar');
    const sidebarOverlay = document.getElementById('sidebarOverlay');
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebarClose = document.getElementById('sidebarClose');
    const dashboardShell = document.querySelector('.dashboard-shell');

    const isMobileViewport = () => window.matchMedia('(max-width: 980px)').matches;

    const closeSidebar = () => {
        if (!sidebar || !sidebarOverlay) {
            return;
        }
        sidebar.classList.remove('open');
        sidebarOverlay.classList.remove('show');
    };

    const openSidebar = () => {
        if (!sidebar || !sidebarOverlay) {
            return;
        }
        sidebar.classList.add('open');
        sidebarOverlay.classList.add('show');
    };

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', () => {
            if (isMobileViewport()) {
                if (!sidebar || !sidebarOverlay) {
                    return;
                }
                const isOpen = sidebar.classList.contains('open');
                if (isOpen) {
                    closeSidebar();
                } else {
                    openSidebar();
                }
            } else {
                if (dashboardShell) {
                    dashboardShell.classList.toggle('sidebar-collapsed');
                }
                closeSidebar();
            }
        });
    }

    if (sidebarClose) {
        sidebarClose.addEventListener('click', closeSidebar);
    }

    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', closeSidebar);
    }

    window.addEventListener('resize', () => {
        if (isMobileViewport()) {
            if (dashboardShell) {
                dashboardShell.classList.remove('sidebar-collapsed');
            }
        } else {
            closeSidebar();
        }
    });

    const faqSection = document.getElementById('faq');
    if (faqSection) {
        const faqItems = Array.from(faqSection.querySelectorAll('.faq-item'));
        const openOrder = faqItems.filter((entry) => entry.open);

        faqItems.forEach((item) => {
            item.addEventListener('toggle', () => {
                if (!item.open) {
                    const closedIndex = openOrder.indexOf(item);
                    if (closedIndex !== -1) {
                        openOrder.splice(closedIndex, 1);
                    }
                    return;
                }

                const existingIndex = openOrder.indexOf(item);
                if (existingIndex !== -1) {
                    openOrder.splice(existingIndex, 1);
                }
                openOrder.push(item);

                while (openOrder.length > 3) {
                    const oldestEntry = openOrder.shift();
                    if (oldestEntry && oldestEntry !== item && oldestEntry.open) {
                        oldestEntry.open = false;
                    }
                }

                const staleOpenItems = faqItems.filter((entry) => entry.open && !openOrder.includes(entry));
                staleOpenItems.forEach((entry) => {
                    if (entry !== item) {
                        entry.open = false;
                    }
                });
            });
        });
    }

    const boardFilterForm = document.querySelector('[data-board-filter-form]');
    if (boardFilterForm) {
        const boardGrid = document.querySelector('[data-board-grid]');
        const boardCards = boardGrid ? Array.from(boardGrid.querySelectorAll('.board-entry-card')) : [];
        const searchInput = boardFilterForm.querySelector('input[name="q"]');
        const sortSelect = boardFilterForm.querySelector('select[name="sort"]');
        const dateFromInput = boardFilterForm.querySelector('input[name="date_from"]');
        const dateToInput = boardFilterForm.querySelector('input[name="date_to"]');
        const emptyState = document.getElementById('boardFilterEmptyState');

        if (boardGrid && boardCards.length) {
            const normalizeNumber = (value) => {
                const parsed = Number.parseInt(String(value || ''), 10);
                return Number.isFinite(parsed) ? parsed : 0;
            };

            const cardMeta = boardCards.map((card) => ({
                card,
                title: (card.getAttribute('data-board-title') || '').toLowerCase(),
                author: (card.getAttribute('data-board-author') || '').toLowerCase(),
                createdDate: card.getAttribute('data-board-created-date') || '',
                timestamp: normalizeNumber(card.getAttribute('data-board-timestamp')),
                upvotes: normalizeNumber(card.getAttribute('data-board-upvotes')),
            }));

            const syncBoardQuery = () => {
                const params = new URLSearchParams(window.location.search);
                const qValue = (searchInput?.value || '').trim();
                const sortValue = (sortSelect?.value || 'most_upvoted').trim().toLowerCase();
                const fromValue = (dateFromInput?.value || '').trim();
                const toValue = (dateToInput?.value || '').trim();

                if (qValue) {
                    params.set('q', qValue);
                } else {
                    params.delete('q');
                }

                if (sortValue && sortValue !== 'most_upvoted') {
                    params.set('sort', sortValue);
                } else {
                    params.delete('sort');
                }

                if (fromValue) {
                    params.set('date_from', fromValue);
                } else {
                    params.delete('date_from');
                }

                if (toValue) {
                    params.set('date_to', toValue);
                } else {
                    params.delete('date_to');
                }

                params.delete('tag');

                const nextQuery = params.toString();
                const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}`;
                window.history.replaceState(null, '', nextUrl);
            };

            const applyBoardFilters = () => {
                const searchTokens = (searchInput?.value || '')
                    .toLowerCase()
                    .trim()
                    .split(/\s+/)
                    .filter(Boolean);

                const fromValue = (dateFromInput?.value || '').trim();
                let toValue = (dateToInput?.value || '').trim();
                if (fromValue && toValue && fromValue > toValue) {
                    toValue = fromValue;
                    if (dateToInput) {
                        dateToInput.value = toValue;
                    }
                }

                const selectedSort = (sortSelect?.value || 'most_upvoted').toLowerCase();
                const visibleCards = [];

                cardMeta.forEach((meta) => {
                    const haystack = `${meta.title} ${meta.author}`;
                    const matchesSearch = !searchTokens.length || searchTokens.every((token) => haystack.includes(token));
                    const matchesFrom = !fromValue || (meta.createdDate && meta.createdDate >= fromValue);
                    const matchesTo = !toValue || (meta.createdDate && meta.createdDate <= toValue);
                    const visible = matchesSearch && matchesFrom && matchesTo;
                    meta.card.hidden = !visible;
                    if (visible) {
                        visibleCards.push(meta);
                    }
                });

                visibleCards.sort((left, right) => {
                    if (selectedSort === 'oldest') {
                        return (left.timestamp - right.timestamp) || (right.upvotes - left.upvotes);
                    }
                    if (selectedSort === 'recent') {
                        return (right.timestamp - left.timestamp) || (right.upvotes - left.upvotes);
                    }
                    return (right.upvotes - left.upvotes) || (right.timestamp - left.timestamp);
                });

                visibleCards.forEach((item) => {
                    boardGrid.appendChild(item.card);
                });

                if (emptyState) {
                    emptyState.hidden = visibleCards.length > 0;
                }

                syncBoardQuery();
            };

            let searchDebounce = null;
            const onSearchInput = () => {
                if (searchDebounce) {
                    window.clearTimeout(searchDebounce);
                }
                searchDebounce = window.setTimeout(() => {
                    applyBoardFilters();
                }, 120);
            };

            boardFilterForm.addEventListener('submit', (event) => {
                event.preventDefault();
                applyBoardFilters();
            });

            if (searchInput) {
                searchInput.addEventListener('input', onSearchInput);
            }
            if (sortSelect) {
                sortSelect.addEventListener('change', applyBoardFilters);
            }
            if (dateFromInput) {
                dateFromInput.addEventListener('change', applyBoardFilters);
            }
            if (dateToInput) {
                dateToInput.addEventListener('change', applyBoardFilters);
            }

            applyBoardFilters();
        }
    }

    const hasPostDetailTriggers = Boolean(document.querySelector('[data-open-post-detail]'));
    const interventionCards = Array.from(document.querySelectorAll('[data-intervention-post-id]'));
    if (hasPostDetailTriggers && interventionCards.length) {
        const detailModal = document.getElementById('interventionDetailModal');
        const detailTitle = document.getElementById('interventionDetailTitle');
        const detailMeta = document.getElementById('interventionDetailMeta');
        const detailTarget = document.getElementById('interventionDetailTarget');
        const detailContent = document.getElementById('interventionDetailContent');
        const detailProblem = document.getElementById('interventionDetailProblem');
        const detailSolution = document.getElementById('interventionDetailSolution');
        const detailReferences = document.getElementById('interventionDetailReferences');
        const detailOutcome = document.getElementById('interventionDetailOutcome');
        const detailLinks = document.getElementById('interventionDetailLinks');
        const detailAttachments = document.getElementById('interventionDetailAttachments');
        const detailProblemWrap = document.getElementById('detailProblemWrap');
        const detailSolutionWrap = document.getElementById('detailSolutionWrap');
        const detailReferencesWrap = document.getElementById('detailReferencesWrap');
        const detailOutcomeWrap = document.getElementById('detailOutcomeWrap');
        const detailLinksWrap = document.getElementById('detailLinksWrap');
        const detailMetricReach = document.getElementById('detailMetricReach');
        const detailMetricOpened = document.getElementById('detailMetricOpened');
        const detailMetricLikes = document.getElementById('detailMetricLikes');
        const detailMetricSaved = document.getElementById('detailMetricSaved');

        let activePostId = null;

        const setMetricValue = (element, value) => {
            if (!element) {
                return;
            }
            element.textContent = Number.isFinite(Number(value)) ? String(value) : '0';
        };

        const setOptionalSection = (wrap, contentNode, value) => {
            if (!wrap || !contentNode) {
                return;
            }
            const hasValue = Boolean((value || '').trim());
            wrap.hidden = !hasValue;
            contentNode.textContent = hasValue ? value : '';
        };

        const clearDetailLists = () => {
            if (detailLinks) {
                detailLinks.innerHTML = '';
            }
            if (detailAttachments) {
                detailAttachments.innerHTML = '';
            }
        };

        const renderDetailLinks = (links) => {
            if (!detailLinks || !Array.isArray(links)) {
                return;
            }
            if (!links.length) {
                detailLinksWrap.hidden = true;
                return;
            }
            detailLinksWrap.hidden = false;
            links.forEach((link) => {
                const row = document.createElement('div');
                row.className = 'intervention-attachment-row';
                const label = document.createElement('span');
                label.textContent = link;

                const actions = document.createElement('div');
                actions.className = 'actions';
                const openBtn = document.createElement('a');
                openBtn.className = 'btn';
                openBtn.href = link;
                openBtn.target = '_blank';
                openBtn.rel = 'noopener';
                openBtn.textContent = 'Open Link';

                actions.appendChild(openBtn);
                row.appendChild(label);
                row.appendChild(actions);
                detailLinks.appendChild(row);
            });
        };

        const renderAttachments = (attachments) => {
            if (!detailAttachments || !Array.isArray(attachments)) {
                return;
            }
            if (!attachments.length) {
                const empty = document.createElement('p');
                empty.className = 'chart-subtitle';
                empty.style.margin = '0';
                empty.textContent = 'No attachments for this intervention.';
                detailAttachments.appendChild(empty);
                return;
            }

            attachments.forEach((attachment) => {
                const row = document.createElement('div');
                row.className = 'intervention-attachment-row';

                const name = document.createElement('span');
                name.textContent = attachment.name || 'Attachment';

                const actions = document.createElement('div');
                actions.className = 'actions';

                const viewBtn = document.createElement('a');
                viewBtn.className = 'btn';
                viewBtn.href = attachment.url;
                viewBtn.target = '_blank';
                viewBtn.rel = 'noopener';
                viewBtn.textContent = 'View';

                const downloadBtn = document.createElement('a');
                downloadBtn.className = 'btn primary';
                downloadBtn.href = attachment.url;
                downloadBtn.setAttribute('download', '');
                downloadBtn.textContent = 'Download';

                actions.appendChild(viewBtn);
                actions.appendChild(downloadBtn);
                row.appendChild(name);
                row.appendChild(actions);
                detailAttachments.appendChild(row);
            });
        };

        const applyDetailPayload = (payload) => {
            if (!payload || !detailModal) {
                return;
            }

            activePostId = payload.id;
            detailTitle.textContent = payload.title || 'Intervention Details';
            detailMeta.textContent = `${payload.author || 'Faculty'} | ${payload.created_at_ist || '-'}`;
            detailContent.textContent = payload.content || '';

            detailTarget.innerHTML = '';
            const targetItems = [
                `Course: ${payload.target?.courses || '-'}`,
                `Semester: ${payload.target?.semesters || '-'}`,
                `Section: ${payload.target?.sections || '-'}`,
            ];
            targetItems.forEach((itemText) => {
                const chip = document.createElement('span');
                chip.className = 'tag-pill';
                chip.textContent = itemText;
                detailTarget.appendChild(chip);
            });

            setOptionalSection(detailProblemWrap, detailProblem, payload.problem_context || '');
            setOptionalSection(detailSolutionWrap, detailSolution, payload.solution_steps || '');
            setOptionalSection(detailReferencesWrap, detailReferences, payload.resource_references || '');
            setOptionalSection(detailOutcomeWrap, detailOutcome, payload.outcome_result || '');

            setMetricValue(detailMetricReach, payload.metrics?.reach || 0);
            setMetricValue(detailMetricOpened, payload.metrics?.opened || 0);
            setMetricValue(detailMetricLikes, payload.metrics?.likes || 0);
            setMetricValue(detailMetricSaved, payload.metrics?.saved || 0);

            clearDetailLists();
            renderDetailLinks(payload.resource_links || []);
            renderAttachments(payload.attachments || []);

            detailModal.classList.add('active');
            if (window.lucide) {
                window.lucide.createIcons();
            }
        };

        const resetTriggerStates = () => {
            document.querySelectorAll('[data-open-post-detail]').forEach((button) => {
                button.classList.remove('is-active');
            });
        };

        document.querySelectorAll('[data-open-post-detail]').forEach((button) => {
            button.addEventListener('click', async () => {
                const postId = Number.parseInt(button.getAttribute('data-post-id') || '', 10);
                const detailUrl = button.getAttribute('data-detail-url') || `/faculty/resource-post/${postId}/detail`;
                if (!Number.isFinite(postId) || !detailModal) {
                    return;
                }

                resetTriggerStates();
                button.classList.add('is-active');

                detailTitle.textContent = 'Loading intervention details...';
                detailMeta.textContent = 'Please wait';
                detailContent.textContent = 'Fetching latest post details and resources.';
                clearDetailLists();
                detailModal.classList.add('active');

                try {
                    const response = await fetch(detailUrl, {
                        headers: { Accept: 'application/json' },
                    });
                    if (!response.ok) {
                        throw new Error(`Request failed with status ${response.status}`);
                    }
                    const payload = await response.json();
                    applyDetailPayload(payload);
                } catch (_error) {
                    detailTitle.textContent = 'Unable to load details';
                    detailMeta.textContent = 'Try again';
                    detailContent.textContent = 'We could not fetch post details right now.';
                }
            });
        });

        const updateCardMetrics = (itemMap) => {
            interventionCards.forEach((card) => {
                const postId = card.getAttribute('data-intervention-post-id');
                if (!postId || !itemMap[postId]) {
                    return;
                }
                const metrics = itemMap[postId];
                card.querySelectorAll('[data-metric-for]').forEach((node) => {
                    const key = node.getAttribute('data-metric-for');
                    if (!key || !(key in metrics)) {
                        return;
                    }
                    node.textContent = String(metrics[key]);
                });

                if (activePostId && Number(postId) === Number(activePostId)) {
                    setMetricValue(detailMetricReach, metrics.reach || 0);
                    setMetricValue(detailMetricOpened, metrics.opened || 0);
                    setMetricValue(detailMetricLikes, metrics.likes || 0);
                    setMetricValue(detailMetricSaved, metrics.saved || 0);
                }
            });
        };

        const fetchLatestMetrics = async () => {
            const metricsUrl = interventionCards
                .map((card) => card.getAttribute('data-metrics-url') || '')
                .find((value) => Boolean(value));
            if (!metricsUrl) {
                return;
            }

            const ids = interventionCards
                .map((card) => card.getAttribute('data-intervention-post-id'))
                .filter(Boolean);
            if (!ids.length) {
                return;
            }

            const params = new URLSearchParams();
            ids.forEach((id) => params.append('post_id', id));
            try {
                const response = await fetch(`${metricsUrl}?${params.toString()}`, {
                    headers: { Accept: 'application/json' },
                });
                if (!response.ok) {
                    return;
                }
                const payload = await response.json();
                updateCardMetrics(payload.items || {});
            } catch (_error) {
                // Keep current values if live refresh fails temporarily.
            }
        };

        fetchLatestMetrics();
        window.setInterval(fetchLatestMetrics, 15000);
    }
});
