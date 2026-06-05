/* ============================================
   云集智能视频创意站 - 官网交互脚本
   ============================================ */

document.addEventListener('DOMContentLoaded', () => {

    // --- 导航栏滚动效果 ---
    const navbar = document.getElementById('navbar');
    const handleScroll = () => {
        navbar.classList.toggle('scrolled', window.scrollY > 40);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();

    // --- 移动端菜单 ---
    const navToggle = document.getElementById('navToggle');
    const navLinks = document.getElementById('navLinks');
    if (navToggle && navLinks) {
        navToggle.addEventListener('click', () => {
            navLinks.classList.toggle('open');
        });
        navLinks.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', () => {
                navLinks.classList.remove('open');
            });
        });
    }

    // --- 滚动渐入动画 ---
    const observerOptions = {
        root: null,
        rootMargin: '0px 0px -60px 0px',
        threshold: 0.1
    };

    const fadeObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                fadeObserver.unobserve(entry.target);
            }
        });
    }, observerOptions);

    // 为功能卡片和优势项添加渐入动画
    document.querySelectorAll('.feature-card, .adv-item, .timeline-item').forEach((el, i) => {
        el.classList.add('fade-in');
        el.style.transitionDelay = `${i * 0.08}s`;
        fadeObserver.observe(el);
    });

    // --- Hero 粒子背景 ---
    const particlesContainer = document.getElementById('particles');
    if (particlesContainer) {
        createParticles(particlesContainer);
    }

    // --- 截图画廊 ---
    initGallery();

    // --- 平滑滚动 ---
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', (e) => {
            e.preventDefault();
            const target = document.querySelector(anchor.getAttribute('href'));
            if (target) {
                const offset = 80;
                const top = target.getBoundingClientRect().top + window.pageYOffset - offset;
                window.scrollTo({ top, behavior: 'smooth' });
            }
        });
    });
});

// --- 粒子背景 ---
function createParticles(container) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    container.appendChild(canvas);

    let width, height, particles;
    const PARTICLE_COUNT = 50;

    function resize() {
        width = canvas.width = container.offsetWidth;
        height = canvas.height = container.offsetHeight;
    }

    function initParticles() {
        particles = [];
        for (let i = 0; i < PARTICLE_COUNT; i++) {
            particles.push({
                x: Math.random() * width,
                y: Math.random() * height,
                vx: (Math.random() - 0.5) * 0.3,
                vy: (Math.random() - 0.5) * 0.3,
                size: Math.random() * 2 + 0.5,
                opacity: Math.random() * 0.3 + 0.1
            });
        }
    }

    function draw() {
        ctx.clearRect(0, 0, width, height);

        particles.forEach(p => {
            p.x += p.vx;
            p.y += p.vy;

            if (p.x < 0) p.x = width;
            if (p.x > width) p.x = 0;
            if (p.y < 0) p.y = height;
            if (p.y > height) p.y = 0;

            ctx.beginPath();
            ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(204, 0, 0, ${p.opacity})`;
            ctx.fill();
        });

        // 连线
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 120) {
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.strokeStyle = `rgba(204, 0, 0, ${0.06 * (1 - dist / 120)})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        }

        requestAnimationFrame(draw);
    }

    resize();
    initParticles();
    draw();

    window.addEventListener('resize', () => {
        resize();
        initParticles();
    });
}

// --- 登录弹窗 ---
function openLoginModal() {
    const modal = document.getElementById('loginModal');
    const iframe = document.getElementById('loginIframe');
    if (!modal || !iframe) return;
    iframe.src = '/sl/connect.php?type=wx';
    modal.classList.add('active');
}

function closeLoginModal() {
    const modal = document.getElementById('loginModal');
    const iframe = document.getElementById('loginIframe');
    if (!modal) return;
    modal.classList.remove('active');
    if (iframe) iframe.src = '';
}

// ESC 关闭弹窗
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeLoginModal();
});

// --- 截图画廊 ---
function initGallery() {
    const thumbs = document.querySelectorAll('.thumb');
    const images = document.querySelectorAll('.gallery-img');

    if (!thumbs.length || !images.length) return;

    thumbs.forEach(thumb => {
        thumb.addEventListener('click', () => {
            const index = thumb.dataset.index;

            // 切换缩略图选中状态
            thumbs.forEach(t => t.classList.remove('active'));
            thumb.classList.add('active');

            // 切换主图
            images.forEach(img => {
                if (img.dataset.index === index) {
                    img.classList.add('active');
                } else {
                    img.classList.remove('active');
                }
            });
        });
    });
}
