import { lessons, lessonCount } from './lessons';

function Hero() {
	return (
		<header className="hero">
			<p className="hero-eyebrow">Harness Engineering for Real Agents</p>
			<h1 className="hero-title">
				Learn Claude Code <span className="hero-title-accent">&mdash; Harness Engineering for Real Agents</span>
			</h1>
			<p className="hero-tagline">The model is the driver. The harness is the vehicle.</p>
			<p className="hero-subtitle">
				A 0-to-1 course in harness engineering. Agency comes from the model;
				a working agent product needs both. This repository teaches you how to
				build the vehicle &mdash; the tools, knowledge, context, and permission
				boundaries that let a trained model operate in the real world.
			</p>
			<div className="hero-actions">
				<a className="btn btn-primary" href="#lessons">
					Start the Journey
				</a>
				<a className="btn btn-ghost" href="#core-idea">
					The Core Idea
				</a>
			</div>
			<div className="hero-stats">
				<span className="hero-stat">
					<strong>{lessonCount}</strong>
					progressive lessons
				</span>
				<span className="hero-stat">
					<strong>1</strong>
					agent loop
				</span>
				<span className="hero-stat">
					<strong>1</strong>
					complete harness
				</span>
			</div>
		</header>
	);
}

function CoreIdea() {
	return (
		<section className="core-idea" id="core-idea">
			<div className="core-idea-text">
				<h2 className="section-heading">Agency is trained. The harness is built.</h2>
				<p>
					Every agent is two things: a <strong>model</strong> that learned to
					perceive, reason, and act through training, and a{' '}
					<strong>harness</strong> &mdash; the code that gives that model an
					operational environment.
				</p>
				<p>
					The model decides. The harness executes. The model reasons. The
					harness provides context. Build the harness well, and the model will
					do the rest.
				</p>
			</div>
			<div className="core-idea-formula">
				<code>Agent = Model + Harness</code>
			</div>
		</section>
	);
}

function LessonCard({ lesson, index }) {
	return (
		<article
			className="lesson-card"
			style={{ '--card-delay': `${(index % 6) * 45}ms` }}
		>
			<div className="lesson-card-meta">
				<span className="lesson-badge">{lesson.number}</span>
				<span className="lesson-position">Lesson {index + 1}</span>
			</div>
			<h3 className="lesson-title">{lesson.title}</h3>
			<p className="lesson-motto">&ldquo;{lesson.motto}&rdquo;</p>
		</article>
	);
}

function CourseGrid() {
	return (
		<section className="course" id="lessons">
			<div className="course-header">
				<h2 className="section-heading">20 Progressive Lessons</h2>
				<p className="course-intro">
					Each lesson adds one harness mechanism on top of the same agent loop.
					The loop never changes &mdash; the mechanisms around it do.
				</p>
			</div>
			<div className="lesson-grid">
				{lessons.map((lesson, index) => (
					<LessonCard key={lesson.number} lesson={lesson} index={index} />
				))}
			</div>
		</section>
	);
}

function Footer() {
	return (
		<footer className="footer">
			<p>
				The model is the driver. The harness is the vehicle.{' '}
				<span className="footer-muted">Bash is all you need.</span>
			</p>
			<p className="footer-muted">
				Learn Claude Code &mdash; Harness Engineering for Real Agents
			</p>
		</footer>
	);
}

export default function App() {
	return (
		<div className="app">
			<Hero />
			<main>
				<CoreIdea />
				<CourseGrid />
			</main>
			<Footer />
		</div>
	);
}
